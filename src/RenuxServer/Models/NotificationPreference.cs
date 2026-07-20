using System.ComponentModel.DataAnnotations;

namespace RenuxServer.Models;

public class NotificationPreference
{
    [Key]
    public Guid Id { get; init; } = Guid.NewGuid();

    public User? User { get; set; }
    [Required]
    public Guid UserId { get; set; }

    [Required]
    public string Topic { get; set; } = null!;

    [Required]
    public bool Enabled { get; set; } = false;

    [Required]
    public string RemindDaysBefore { get; set; } = "7,1,0";

    [Required]
    public string Channel { get; set; } = "in_app";

    [Required]
    public DateTime CreatedTime { get; init; } = DateTime.UtcNow;

    [Required]
    public DateTime UpdatedTime { get; set; } = DateTime.UtcNow;
}
