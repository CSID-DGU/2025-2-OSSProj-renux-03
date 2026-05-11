using AutoMapper;
using Microsoft.EntityFrameworkCore;
using System.Net.Http.Json;
using System.Security.Claims;
using System.Text.Json;

using RenuxServer.DbContexts;
using RenuxServer.Dtos.ChatDtos;
using RenuxServer.Models;
using RenuxServer.Dtos.EtcDtos;

namespace RenuxServer.Apis.Chat;

public record StartChat(OrganizationDto Org, string Title);
public record LoadChat(Guid ChatId, DateTime LastTime);
public record ToRag(string SessionId, string Question, string? Major = null);

static public class ChatRequestApis
{
    private const string DefaultFallbackAnswer = "죄송합니다. 답변을 생성할 수 없습니다.";

    static public void AddChatApis(this WebApplication application)
    {
        var app = application.MapGroup("/chat");

        app.MapGet("/active", async (ServerDbContext db, HttpContext context, IMapper mapper) =>
        {
            // For guests, return an empty list as their chats are not persisted
            if (!context.Request.Cookies.ContainsKey("renux-server-token"))
            {
                return Results.Ok(new List<ActiveChatDto>());
            }

            Guid id;
            var userIdStr = context.User.FindFirstValue(Microsoft.IdentityModel.JsonWebTokens.JwtRegisteredClaimNames.Sub);
            if (userIdStr == null || !Guid.TryParse(userIdStr, out id))
            {
                return Results.Unauthorized();
            }

            List<ActiveChatDto> chats =
            mapper.Map<List<ActiveChatDto>>(
                await db.Chats
                .Include(ch => ch.Organization)
                .Where(ch => Equals(ch.UserId, id))
                .ToListAsync()
                );

            return Results.Ok(chats);
        });

        app.MapPost("/start", async (ServerDbContext db, HttpContext context, StartChat stch, IMapper mapper) =>
        {
            DateTime time = DateTime.Now.ToUniversalTime();
            Guid id = Guid.NewGuid();

            // Authenticated user check
            bool isAuthenticated = context.Request.Cookies.ContainsKey("renux-server-token");

            if (!isAuthenticated)
            {
                // Guest flow: Do NOT save to DB
                // Create a temporary ID for the frontend session
                Guid guestChatId = Guid.NewGuid();
                
                // Ensure the guest cookie exists for session consistency (optional but good practice)
                if (!context.Request.Cookies.ContainsKey("renux-server-guest"))
                {
                    CookieOptions opt = new() { HttpOnly = true, SameSite = SameSiteMode.Strict };
                    context.Response.Cookies.Append("renux-server-guest", Guid.NewGuid().ToString(), opt);
                }

                ActiveChatDto guestChatDto = new()
                {
                    Id = guestChatId,
                    Organization = stch.Org,
                    Title = stch.Title
                };
                return Results.Ok(guestChatDto);
            }

            // Authenticated User Flow
            while (await db.Chats.AnyAsync(c => c.Id == id))
                id = Guid.NewGuid();

            var userIdStr = context.User.FindFirstValue(Microsoft.IdentityModel.JsonWebTokens.JwtRegisteredClaimNames.Sub);
            if (userIdStr == null || !Guid.TryParse(userIdStr, out Guid userId))
            {
                return Results.Unauthorized();
            }

            ActiveChat chat = new()
            {
                Id = id,
                UserId = userId,
                OrganizationId = stch.Org.Id,
                Title = stch.Title,
                CreatedTime = time,
                UpdatedTime = time
            };

                ChatMessage startChat = new()
            {
                ChatId = chat.Id,
                IsAsk = false,
                Content = "안녕하세요. 동똑이입니다. 무엇을 도와드릴까요?",
                CreatedTime = time
            };

            await db.Chats.AddAsync(chat);
            await db.ChatMessages.AddAsync(startChat);
            await db.SaveChangesAsync();

            ActiveChatDto chatDto = mapper.Map<ActiveChatDto>(chat);

            return Results.Ok(chatDto);
        });

        app.MapPost("/msg", async (ServerDbContext db, HttpContext context, ChatMessageDto askDto, IMapper mapper, ILogger<Program> logger, IConfiguration configuration, IHttpClientFactory httpClientFactory) =>
        {
            bool isAuthenticated = context.Request.Cookies.ContainsKey("renux-server-token");

            if (!isAuthenticated)
            {
                // Guest Flow: Do NOT save to DB
                ToRag toRag = new(askDto.ChatId.ToString(), askDto.Content);
                RagReplyDto replyObj = await AskRagAsync(httpClientFactory, configuration, logger, toRag);
                logger.LogInformation("Guest AI Reply: {ReplyContent}", replyObj.Answer);

                ChatMessageDto replyDto = new()
                {
                    ChatId = askDto.ChatId,
                    Content = replyObj.Answer,
                    Citations = replyObj.Citations,
                    Route = replyObj.Route,
                    Sources = replyObj.Sources,
                    IsAsk = false,
                    CreatedTime = DateTime.Now.ToUniversalTime()
                };
                return Results.Ok(replyDto);
            }

            // Authenticated Flow
            if (!TryGetUserId(context, out var currentUserId))
            {
                return Results.Unauthorized();
            }

            var chat = await db.Chats
                .Include(c => c.Organization)
                .ThenInclude(o => o!.Major)
                .FirstOrDefaultAsync(c => c.Id == askDto.ChatId && c.UserId == currentUserId);
            if (chat is null)
            {
                return Results.NotFound();
            }

            ChatMessage ask = mapper.Map<ChatMessage>(askDto);
            await db.ChatMessages.AddAsync(ask);

            string? majorName = chat?.Organization?.Major?.Majorname;
            ToRag authToRag = new(askDto.ChatId.ToString(), askDto.Content, majorName);
            RagReplyDto ragReply = await AskRagAsync(httpClientFactory, configuration, logger, authToRag);
            logger.LogInformation("Authenticated AI Reply: {Reply}", ragReply.Answer);

            ChatMessage apply = new()
            {
                ChatId = ask.ChatId,
                Content = ragReply.Answer,
                Citations = ragReply.Citations,
                RouteData = SerializeOptional(ragReply.Route),
                SourcesData = SerializeOptional(ragReply.Sources),
                IsAsk = false,
                CreatedTime = DateTime.Now.ToUniversalTime()
            };

            await db.ChatMessages.AddAsync(apply);
            await db.SaveChangesAsync();

            ChatMessageDto applyDto = ToChatMessageDto(apply);
            return Results.Ok(applyDto);
        });

        app.MapPost("/load", async (ServerDbContext db, HttpContext context, IMapper mapper, LoadChat load) =>
        {
            // For guests, return empty list (no persistence)
            if (!context.Request.Cookies.ContainsKey("renux-server-token"))
            {
                return Results.Ok(new List<ChatMessageDto>());
            }

            if (!TryGetUserId(context, out var userId))
            {
                return Results.Unauthorized();
            }

            bool ownsChat = await db.Chats.AnyAsync(c => c.Id == load.ChatId && c.UserId == userId);
            if (!ownsChat)
            {
                return Results.NotFound();
            }

            List<ChatMessageDto> chatMessages = await MessagesToList(db, mapper, load.LastTime, load.ChatId);
            return Results.Ok(chatMessages);
        });

        app.MapDelete("/{chatId}", async (ServerDbContext db, HttpContext context, Guid chatId) =>
        {
            if (context.Request.Cookies.ContainsKey("renux-server-token"))
            {
                var userIdStr = context.User.FindFirstValue(Microsoft.IdentityModel.JsonWebTokens.JwtRegisteredClaimNames.Sub);
                if (userIdStr == null || !Guid.TryParse(userIdStr, out var userId))
                {
                    return Results.Unauthorized();
                }

                var chat = await db.Chats.FirstOrDefaultAsync(c => c.Id == chatId && c.UserId == userId);

                if (chat != null)
                {
                    var messages = db.ChatMessages.Where(m => m.ChatId == chatId);
                    db.ChatMessages.RemoveRange(messages);

                    db.Chats.Remove(chat);
                    await db.SaveChangesAsync();

                    return Results.Ok();
                }
                return Results.NotFound();
            }

            return Results.Ok();
        });
    }

    static public async Task<List<ChatMessageDto>> MessagesToList(ServerDbContext db, IMapper mapper, DateTime lastTime, Guid chatId)
    {
        List<ChatMessage> messages = await db.ChatMessages
            .Where(cm => Equals(cm.ChatId, chatId) && cm.CreatedTime < lastTime)
            .OrderByDescending(cm => cm.CreatedTime)
            .Take(20)
            .ToListAsync();

        return messages.Select(ToChatMessageDto).ToList();
    }

    static private bool TryGetUserId(HttpContext context, out Guid userId)
    {
        var userIdStr = context.User.FindFirstValue(Microsoft.IdentityModel.JsonWebTokens.JwtRegisteredClaimNames.Sub);
        return Guid.TryParse(userIdStr, out userId);
    }

    static private ChatMessageDto ToChatMessageDto(ChatMessage message)
    {
        return new ChatMessageDto
        {
            Id = message.Id,
            ChatId = message.ChatId,
            IsAsk = message.IsAsk,
            Content = message.Content,
            Citations = message.Citations,
            Route = DeserializeOptional<List<string>>(message.RouteData),
            Sources = DeserializeOptional<List<SourceChunkDto>>(message.SourcesData),
            CreatedTime = message.CreatedTime
        };
    }

    static private string? SerializeOptional<T>(T value)
    {
        return value is null ? null : JsonSerializer.Serialize(value);
    }

    static private async Task<RagReplyDto> AskRagAsync(
        IHttpClientFactory httpClientFactory,
        IConfiguration configuration,
        ILogger logger,
        ToRag payload)
    {
        var client = httpClientFactory.CreateClient();
        client.Timeout = TimeSpan.FromSeconds(configuration.GetValue("RagServiceTimeoutSeconds", 120));
        var ragUrl = (configuration["RagServiceUrl"] ?? "http://rag-service:8000").TrimEnd('/');

        try
        {
            using var res = await client.PostAsJsonAsync($"{ragUrl}/ask", payload);
            if (!res.IsSuccessStatusCode)
            {
                logger.LogWarning("RAG request failed with status {StatusCode}", res.StatusCode);
                return new RagReplyDto { Answer = DefaultFallbackAnswer };
            }

            var reply = await res.Content.ReadFromJsonAsync<RagReplyDto>();
            if (reply is null || string.IsNullOrWhiteSpace(reply.Answer))
            {
                logger.LogWarning("RAG returned an empty response.");
                return new RagReplyDto { Answer = DefaultFallbackAnswer };
            }

            return reply;
        }
        catch (TaskCanceledException exc)
        {
            logger.LogWarning(exc, "RAG request timed out.");
            return new RagReplyDto { Answer = DefaultFallbackAnswer };
        }
        catch (HttpRequestException exc)
        {
            logger.LogWarning(exc, "RAG request failed.");
            return new RagReplyDto { Answer = DefaultFallbackAnswer };
        }
        catch (JsonException exc)
        {
            logger.LogWarning(exc, "RAG response JSON parsing failed.");
            return new RagReplyDto { Answer = DefaultFallbackAnswer };
        }
    }

    static private T? DeserializeOptional<T>(string? value)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            return default;
        }

        try
        {
            return JsonSerializer.Deserialize<T>(value);
        }
        catch
        {
            return default;
        }
    }
}
