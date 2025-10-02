using System.ComponentModel.DataAnnotations;

namespace RenuexServer.Models;

public class ChatMessage
{
    [Key]
    public Guid Id { get; init; }

    public ChatKind? Conversation { get; set; }     // 외래키
    [Required]
    public Guid ConversationId { get; init; }
    
    [Required]
    public bool IsAsk { get; set; } = true;
    [Required]
    public string Content { get; set; } = null!;
    [Required]
    public DateTime CreatedTime { get; init; } = DateTime.Now;
}