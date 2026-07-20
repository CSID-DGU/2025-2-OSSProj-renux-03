using System;
using Microsoft.EntityFrameworkCore.Infrastructure;
using Microsoft.EntityFrameworkCore.Migrations;
using RenuxServer.DbContexts;

#nullable disable

namespace RenuxServer.Migrations
{
    [DbContext(typeof(ServerDbContext))]
    [Migration("20260720020000_AddChatCompletionMetadataAndProductEvents")]
    public partial class AddChatCompletionMetadataAndProductEvents : Migration
    {
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.AddColumn<bool>(
                name: "grounded",
                table: "chat_messages",
                type: "boolean",
                nullable: true);

            migrationBuilder.AddColumn<double>(
                name: "grounding_score",
                table: "chat_messages",
                type: "double precision",
                nullable: true);

            migrationBuilder.AddColumn<string>(
                name: "request_id",
                table: "chat_messages",
                type: "text",
                nullable: true);

            migrationBuilder.AddColumn<string>(
                name: "suggested_questions_json",
                table: "chat_messages",
                type: "text",
                nullable: true);

            migrationBuilder.CreateTable(
                name: "product_events",
                columns: table => new
                {
                    id = table.Column<Guid>(type: "uuid", nullable: false),
                    event_type = table.Column<string>(type: "character varying(40)", maxLength: 40, nullable: false),
                    idempotency_key = table.Column<string>(type: "character varying(160)", maxLength: 160, nullable: false),
                    subject_key = table.Column<string>(type: "character varying(100)", maxLength: 100, nullable: true),
                    session_key = table.Column<string>(type: "character varying(100)", maxLength: 100, nullable: true),
                    answer_key = table.Column<string>(type: "character varying(100)", maxLength: 100, nullable: true),
                    rating = table.Column<int>(type: "integer", nullable: true),
                    suggestion_index = table.Column<int>(type: "integer", nullable: true),
                    suggestion_count = table.Column<int>(type: "integer", nullable: true),
                    is_fallback = table.Column<bool>(type: "boolean", nullable: true),
                    grounded = table.Column<bool>(type: "boolean", nullable: true),
                    source_count = table.Column<int>(type: "integer", nullable: true),
                    is_excluded = table.Column<bool>(type: "boolean", nullable: false, defaultValue: false),
                    exclusion_reason = table.Column<string>(type: "character varying(40)", maxLength: 40, nullable: true),
                    occurred_time = table.Column<DateTime>(type: "timestamp with time zone", nullable: false)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_product_events", x => x.id);
                });

            migrationBuilder.CreateIndex(
                name: "IX_chat_messages_request_id",
                table: "chat_messages",
                column: "request_id",
                unique: true,
                filter: "request_id IS NOT NULL");

            migrationBuilder.CreateIndex(
                name: "IX_product_events_answer_key_event_type",
                table: "product_events",
                columns: new[] { "answer_key", "event_type" });

            migrationBuilder.CreateIndex(
                name: "IX_product_events_event_type_occurred_time",
                table: "product_events",
                columns: new[] { "event_type", "occurred_time" });

            migrationBuilder.CreateIndex(
                name: "IX_product_events_idempotency_key",
                table: "product_events",
                column: "idempotency_key",
                unique: true);

            migrationBuilder.CreateIndex(
                name: "IX_product_events_subject_key_occurred_time",
                table: "product_events",
                columns: new[] { "subject_key", "occurred_time" });
        }

        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropTable(name: "product_events");

            migrationBuilder.DropIndex(
                name: "IX_chat_messages_request_id",
                table: "chat_messages");

            migrationBuilder.DropColumn(name: "grounded", table: "chat_messages");
            migrationBuilder.DropColumn(name: "grounding_score", table: "chat_messages");
            migrationBuilder.DropColumn(name: "request_id", table: "chat_messages");
            migrationBuilder.DropColumn(name: "suggested_questions_json", table: "chat_messages");
        }
    }
}
