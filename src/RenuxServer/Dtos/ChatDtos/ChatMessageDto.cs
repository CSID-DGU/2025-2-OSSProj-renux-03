namespace RenuxServer.Dtos.ChatDtos;

public class ChatMessageDto
{
    public Guid Id { get; init; }
    public Guid ChatId { get; init; }

    public bool IsAsk { get; init; }

    public string Content { get; init; } = null!;

    public string? Citations { get; init; }

    public List<string>? Route { get; init; }

    public List<SourceChunkDto>? Sources { get; init; }

    public DateTime CreatedTime { get; init; }
}
