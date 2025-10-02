using RenuexServer.Models;

namespace RenuexServer.Dtos.AuthDtos;

public class SignupUserDto
{
    public string UserId { get; init; } = null!;
    public string Password { get; set; } = null!;
    public string Username { get; set; } = null!;

    public Major? Major { get; set; }
    public string Role { get; set; } = null!;
}
