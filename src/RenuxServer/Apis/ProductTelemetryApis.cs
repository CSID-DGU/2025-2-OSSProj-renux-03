using Microsoft.EntityFrameworkCore;
using Microsoft.AspNetCore.DataProtection;
using System.IdentityModel.Tokens.Jwt;
using System.Security.Claims;
using System.Text.Json;

using RenuxServer.DbContexts;
using RenuxServer.Models;
using RenuxServer.Services;

namespace RenuxServer.Apis;

public sealed record ProductEventRequest(
    string EventType,
    string RequestId,
    int? SuggestionIndex,
    int? SuggestionCount);

public static class ProductTelemetryApis
{
    public static void AddProductTelemetryApis(this WebApplication application)
    {
        var app = application.MapGroup("/telemetry").RequireRateLimiting("chat");

        app.MapPost("/events", async (
            ProductEventRequest request,
            ServerDbContext db,
            HttpContext context,
            IConfiguration configuration,
            ILogger<Program> logger,
            IDataProtectionProvider dataProtectionProvider) =>
        {
            if (request.EventType is not (ProductEventTypes.SuggestionShown or ProductEventTypes.SuggestionClicked))
            {
                return Results.BadRequest(new { message = "허용되지 않은 이벤트입니다." });
            }
            if (string.IsNullOrWhiteSpace(request.RequestId) || request.RequestId.Length > 200)
            {
                return Results.BadRequest(new { message = "유효한 requestId가 필요합니다." });
            }

            Guid? verifiedSessionId = null;
            int? actualSuggestionCount = null;
            string? validatedGuestSubjectId = null;
            if (context.User.Identity?.IsAuthenticated == true)
            {
                string? subjectClaim = context.User.FindFirstValue(JwtRegisteredClaimNames.Sub);
                if (!Guid.TryParse(subjectClaim, out Guid userId)) return Results.Unauthorized();

                var ownedAnswer = await (
                    from answer in db.ChatMessages
                    join chat in db.Chats on answer.ChatId equals chat.Id
                    where !answer.IsAsk
                          && answer.IsCurrent
                          && answer.RequestId == request.RequestId
                          && chat.UserId == userId
                    select new { answer.ChatId, answer.SuggestedQuestionsJson })
                    .FirstOrDefaultAsync(context.RequestAborted);
                if (ownedAnswer is null) return Results.NotFound();
                verifiedSessionId = ownedAnswer.ChatId;
                actualSuggestionCount = CountSuggestions(ownedAnswer.SuggestedQuestionsJson);
            }
            else if (!GuestIdentity.TryValidate(context.Request, dataProtectionProvider, out validatedGuestSubjectId))
            {
                return Results.BadRequest(new { message = "유효한 게스트 세션이 필요합니다." });
            }

            var eventContext = await ProductTelemetry.ResolveContextAsync(
                db,
                context,
                configuration,
                verifiedSessionId,
                validatedGuestSubjectId,
                context.RequestAborted);
            ProductEvent? completionEvent = await ProductTelemetry.FindAnswerCompletionEventAsync(
                    db,
                    configuration,
                    eventContext,
                    request.RequestId,
                    context.RequestAborted);
            if (completionEvent is null)
            {
                // Missing analytics history must not block a suggestion click,
                // but incomplete/forged streams must not become product events.
                return Results.Accepted(value: new { accepted = false, reason = "completion_not_observed" });
            }
            actualSuggestionCount ??= completionEvent.SuggestionCount;
            if (actualSuggestionCount is null
                || !SuggestionIntegrity.Matches(
                    request.EventType,
                    actualSuggestionCount.Value,
                    request.SuggestionCount,
                    request.SuggestionIndex))
            {
                return Results.BadRequest(new { message = "완료 답변의 추천질문 범위와 일치하지 않습니다." });
            }
            var data = new ProductEventData(
                request.EventType,
                request.RequestId,
                verifiedSessionId,
                SuggestionIndex: request.SuggestionIndex,
                SuggestionCount: request.SuggestionCount);
            if (!ProductTelemetry.IsValidEventData(data))
            {
                return Results.BadRequest(new { message = "이벤트 속성이 유효하지 않습니다." });
            }

            try
            {
                bool inserted = await ProductTelemetry.RecordAsync(
                    db,
                    configuration,
                    eventContext,
                    data,
                    context.RequestAborted);
                return Results.Accepted(value: new { accepted = true, duplicate = !inserted });
            }
            catch (Exception exception)
            {
                // Analytics must never block the chat experience or expose its payload.
                logger.LogWarning(exception, "Product telemetry event write failed. EventType={EventType}", request.EventType);
                return Results.StatusCode(StatusCodes.Status503ServiceUnavailable);
            }
        });
    }

    private static int? CountSuggestions(string? questionsJson)
    {
        if (string.IsNullOrWhiteSpace(questionsJson)) return null;
        try
        {
            return JsonSerializer.Deserialize<List<string>>(questionsJson)?.Count;
        }
        catch (JsonException)
        {
            return null;
        }
    }
}
