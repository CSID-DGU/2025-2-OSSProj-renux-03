export const GUEST_TOKEN_HEADER = 'X-Guest-Token'

export const withGuestTokenHeader = (
  headers: Record<string, string>,
  guestToken?: string,
): Record<string, string> => (
  guestToken
    ? { ...headers, [GUEST_TOKEN_HEADER]: guestToken }
    : headers
)
