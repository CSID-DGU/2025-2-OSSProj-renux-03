using System.ComponentModel.DataAnnotations;

namespace RenuxServer.Models;

public class UserNotification
{
    [Key]
    public Guid Id { get; init; } = Guid.NewGuid();

    public User? User { get; set; }
    [Required]
    public Guid UserId { get; set; }

    [Required]
    public string Topic { get; set; } = null!;

    [Required]
    public string Source { get; set; } = null!;

    [Required]
    public string SourceId { get; set; } = null!;

    [Required]
    public string DedupKey { get; set; } = null!;

    [Required]
    public string Title { get; set; } = null!;

    [Required]
    public string Body { get; set; } = null!;

    [Required]
    public DateTime TargetDate { get; set; }

    [Required]
    public DateTime ReminderDate { get; set; }

    [Required]
    public int ReminderDaysBefore { get; set; }

    public string? Url { get; set; }

    [Required]
    public bool IsRead { get; set; } = false;

    [Required]
    public DateTime CreatedTime { get; init; } = DateTime.UtcNow;

    public DateTime? ReadTime { get; set; }
}
