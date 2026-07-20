using Microsoft.AspNetCore.DataProtection;
using System.Security.Cryptography;

namespace RenuxServer.Services;

public static class GuestIdentity
{
    public const string CookieName = "renux-server-guest";
    private const string ProtectorPurpose = "RenuxServer.GuestIdentity.v1";
    private const string PayloadPrefix = "v1:";
    private const int MaxProtectedCookieLength = 512;

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
        guestSubjectId = string.Empty;
        if (!request.Cookies.TryGetValue(CookieName, out string? protectedValue)
            || string.IsNullOrWhiteSpace(protectedValue)
            || protectedValue.Length > MaxProtectedCookieLength)
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
