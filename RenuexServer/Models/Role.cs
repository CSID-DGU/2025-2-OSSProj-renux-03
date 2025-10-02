﻿using System.ComponentModel.DataAnnotations;

namespace RenuexServer.Models;

public class Role
{
    [Key]
    public Guid Id { get; init; }
    [Required]
    public string Rolename { get; set; } = null!;
}
