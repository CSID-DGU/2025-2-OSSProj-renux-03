namespace RenuxServer.Dtos.ChatDtos;

public class RagReplyDto
{
    public string Answer { get; init; } = string.Empty;
    public string? Citations { get; init; }
    public List<string>? Route { get; init; }
    public List<SourceChunkDto>? Sources { get; init; }
}
