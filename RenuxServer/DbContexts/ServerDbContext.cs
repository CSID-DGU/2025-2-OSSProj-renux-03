using Microsoft.EntityFrameworkCore;
using RenuexServer.Models;

namespace RenuexServer.DbContexts;

public class ServerDbContext : DbContext
{
    public ServerDbContext(DbContextOptions<ServerDbContext> options) : base(options) { }

    public DbSet<User> Users { get; set; }
    public DbSet<ChatKind> ChatKinds { get; set; }
    public DbSet<ChatMessage> ChatMessages { get; set; }
    public DbSet<Organization> Organizations { get; set; }
    public DbSet<Major> Majors { get; set; }
    public DbSet<Role> Roles { get; set; }

    protected override void OnModelCreating(ModelBuilder modelBuilder)
    {
        base.OnModelCreating(modelBuilder);

        var users = modelBuilder.Entity<User>();
        var chatKind = modelBuilder.Entity<ChatKind>();
        var message = modelBuilder.Entity<ChatMessage>();
        var org = modelBuilder.Entity<Organization>();
        var depart = modelBuilder.Entity<Major>();
        var role = modelBuilder.Entity<Role>();

        users.ToTable("users").HasIndex(p => p.UserId).IsUnique();
        chatKind.ToTable("chat_kinds");
        message.ToTable("chat_messages");
        org.ToTable("organizations");
        depart.ToTable("majors");
        role.ToTable("roles");

        users.Property(u => u.Id).HasColumnName("id");
        users.Property(u => u.UserId).HasColumnName("user_id");
        users.Property(u => u.HashPassword).HasColumnName("password");
        users.Property(u => u.Username).HasColumnName("user_name");
        users.Property(u => u.MajorId).HasColumnName("major_id");
        users.Property(u => u.RoleId).HasColumnName("role");
        users.Property(u => u.CreatedTime).HasColumnName("created_time");
        users.Property(u => u.UpdatedTime).HasColumnName("updated_time");

        chatKind.Property(c => c.Id).HasColumnName("id");
        chatKind.Property(c => c.UserId).HasColumnName("user_id");
        chatKind.Property(c => c.OrganizationId).HasColumnName("organization_id");
        chatKind.Property(c => c.Title).HasColumnName("title");
        chatKind.Property(c => c.CreatedTime).HasColumnName("created_time");
        chatKind.Property(c => c.UpdatedTime).HasColumnName("updated_time");

        message.Property(c => c.Id).HasColumnName("id");
        message.Property(c => c.ConversationId).HasColumnName("conversation_id");
        message.Property(c => c.IsAsk).HasColumnName("is_ask");
        message.Property(c => c.Content).HasColumnName("content");
        message.Property(c => c.CreatedTime).HasColumnName("created_time");

        org.Property(o => o.Id).HasColumnName("id");
        org.Property(o => o.MajorId).HasColumnName("major_id");
        org.Property(o => o.IsActive).HasColumnName("is_active");
        org.Property(o => o.CreatedTime).HasColumnName("created_time");
        org.Property(o => o.UpdatedTime).HasColumnName("updated_time");

        depart.Property(d => d.Id).HasColumnName("id");
        depart.Property(d => d.DepartmentName).HasColumnName("department_name");

        role.Property(r => r.Id).HasColumnName("id");
        role.Property(r => r.Rolename).HasColumnName("role_name");
    }
}
