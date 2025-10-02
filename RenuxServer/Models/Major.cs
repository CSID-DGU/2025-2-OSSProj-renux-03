using System.ComponentModel.DataAnnotations;

namespace RenuexServer.Models;

public class Major
{
    [Key]
    public Guid Id { get; init; } 
    [Required]
    public string DepartmentName { get; set; } = null!;
}
