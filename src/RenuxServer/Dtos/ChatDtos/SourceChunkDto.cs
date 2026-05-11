namespace RenuxServer.Dtos.ChatDtos;

public class SourceChunkDto
{
    public string Source { get; init; } = string.Empty;
    public Dictionary<string, object?> Metadata { get; init; } = [];
    public string Snippet { get; init; } = string.Empty;
}
