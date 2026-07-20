using System;
using Microsoft.EntityFrameworkCore.Infrastructure;
using Microsoft.EntityFrameworkCore.Migrations;
using RenuxServer.DbContexts;

#nullable disable

namespace RenuxServer.Migrations
{
    /// <inheritdoc />
    [DbContext(typeof(ServerDbContext))]
    [Migration("20260720010000_AddChatAnswerVersionTimestamp")]
    public partial class AddChatAnswerVersionTimestamp : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.AddColumn<DateTime>(
                name: "version_created_time",
                table: "chat_messages",
                type: "timestamp with time zone",
                nullable: true);

            migrationBuilder.Sql("""
                UPDATE chat_messages
                SET version_created_time = created_time
                WHERE is_ask = FALSE
                  AND parent_question_id IS NOT NULL
                  AND answer_version IS NOT NULL;
                """);
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropColumn(
                name: "version_created_time",
                table: "chat_messages");
        }
    }
}
