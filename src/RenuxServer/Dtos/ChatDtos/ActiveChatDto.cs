using RenuxServer.Dtos.EtcDtos;
using RenuxServer.Models;
using System.Text.Json.Serialization;

namespace RenuxServer.Dtos.ChatDtos;

public class ActiveChatDto
{
    public Guid Id { get; init; }

    public OrganizationDto Organization { get; init; } = null!;

    public string Title { get; set; } = null!;

    [JsonIgnore(Condition = JsonIgnoreCondition.WhenWritingNull)]
    public string? GuestToken { get; init; }
}
