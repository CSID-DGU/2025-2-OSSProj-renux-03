using Microsoft.AspNetCore.DataProtection;
using System.Security.Cryptography;

namespace RenuxServer.Services;

public static class GuestIdentity
{
    public const string CookieName = "renux-server-guest";
    public const string HeaderName = "X-Guest-Token";
    private const string ProtectorPurpose = "RenuxServer.GuestIdentity.v1";
    private const string PayloadPrefix = "v1:";
    private const int MaxProtectedTokenLength = 512;

    public static string Issue(IDataProtectionProvider provider)
    {
        string payload = $"{PayloadPrefix}{Guid.NewGuid():N}";
        return provider.CreateProtector(ProtectorPurpose).Protect(payload);
    }

    public static bool TryValidate(
        HttpRequest request,
        IDataProtectionProvider provider,
        out string guestSubjectId)
    {
        return TryValidate(request, provider, out guestSubjectId, out _);
    }

    public static bool TryValidate(
        HttpRequest request,
        IDataProtectionProvider provider,
        out string guestSubjectId,
        out string guestToken)
    {
        guestSubjectId = string.Empty;
        guestToken = string.Empty;

        if (request.Headers.TryGetValue(HeaderName, out var headerValues)
            && headerValues.Count == 1
            && TryValidateProtectedValue(headerValues[0], provider, out guestSubjectId))
        {
            guestToken = headerValues[0]!;
            return true;
        }

        if (request.Cookies.TryGetValue(CookieName, out string? cookieValue)
            && TryValidateProtectedValue(cookieValue, provider, out guestSubjectId))
        {
            guestToken = cookieValue;
            return true;
        }

        guestSubjectId = string.Empty;
        return false;
    }

    private static bool TryValidateProtectedValue(
        string? protectedValue,
        IDataProtectionProvider provider,
        out string guestSubjectId)
    {
        guestSubjectId = string.Empty;
        if (string.IsNullOrWhiteSpace(protectedValue)
            || protectedValue.Length > MaxProtectedTokenLength)
        {
            return false;
        }

        try
        {
            string payload = provider.CreateProtector(ProtectorPurpose).Unprotect(protectedValue);
            if (!payload.StartsWith(PayloadPrefix, StringComparison.Ordinal)) return false;

            string rawId = payload[PayloadPrefix.Length..];
            if (rawId.Length != 32 || !Guid.TryParseExact(rawId, "N", out Guid parsed)) return false;

            guestSubjectId = parsed.ToString("N");
            return true;
        }
        catch (Exception exception) when (exception is CryptographicException or FormatException)
        {
            return false;
        }
    }
}
