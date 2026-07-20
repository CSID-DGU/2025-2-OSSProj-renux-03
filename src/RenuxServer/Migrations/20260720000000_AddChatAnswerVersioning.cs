using System;
using Microsoft.EntityFrameworkCore.Infrastructure;
using Microsoft.EntityFrameworkCore.Migrations;
using RenuxServer.DbContexts;

#nullable disable

namespace RenuxServer.Migrations
{
    /// <inheritdoc />
    [DbContext(typeof(ServerDbContext))]
    [Migration("20260720000000_AddChatAnswerVersioning")]
    public partial class AddChatAnswerVersioning : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.AddColumn<Guid>(
                name: "parent_question_id",
                table: "chat_messages",
                type: "uuid",
                nullable: true);

            migrationBuilder.AddColumn<int>(
                name: "answer_version",
                table: "chat_messages",
                type: "integer",
                nullable: true);

            migrationBuilder.AddColumn<bool>(
                name: "is_current",
                table: "chat_messages",
                type: "boolean",
                nullable: false,
                defaultValue: true);

            // Associate every legacy assistant message with the latest question
            // in the same chat. Messages before the first question (the welcome
            // message) intentionally remain ungrouped and visible.
            migrationBuilder.Sql("""
                WITH answer_parents AS (
                    SELECT
                        answer.id AS answer_id,
                        (
                            SELECT question.id
                            FROM chat_messages AS question
                            WHERE question.chat_id = answer.chat_id
                              AND question.is_ask = TRUE
                              AND question.created_time <= answer.created_time
                            ORDER BY question.created_time DESC, question.id DESC
                            LIMIT 1
                        ) AS parent_question_id
                    FROM chat_messages AS answer
                    WHERE answer.is_ask = FALSE
                ),
                ranked_answers AS (
                    SELECT
                        answer.id AS answer_id,
                        answer_parents.parent_question_id,
                        ROW_NUMBER() OVER (
                            PARTITION BY answer_parents.parent_question_id
                            ORDER BY answer.created_time, answer.id
                        )::integer AS answer_version,
                        ROW_NUMBER() OVER (
                            PARTITION BY answer_parents.parent_question_id
                            ORDER BY answer.created_time DESC, answer.id DESC
                        ) = 1 AS is_current
                    FROM chat_messages AS answer
                    INNER JOIN answer_parents ON answer_parents.answer_id = answer.id
                    WHERE answer_parents.parent_question_id IS NOT NULL
                )
                UPDATE chat_messages AS answer
                SET parent_question_id = ranked_answers.parent_question_id,
                    answer_version = ranked_answers.answer_version,
                    is_current = ranked_answers.is_current
                FROM ranked_answers
                WHERE answer.id = ranked_answers.answer_id;
                """);

            migrationBuilder.CreateIndex(
                name: "IX_chat_messages_parent_question_id_answer_version",
                table: "chat_messages",
                columns: new[] { "parent_question_id", "answer_version" },
                unique: true,
                filter: "parent_question_id IS NOT NULL AND answer_version IS NOT NULL AND is_ask = FALSE");

            migrationBuilder.CreateIndex(
                name: "IX_chat_messages_parent_question_id",
                table: "chat_messages",
                column: "parent_question_id",
                unique: true,
                filter: "parent_question_id IS NOT NULL AND is_ask = FALSE AND is_current = TRUE");

            migrationBuilder.AddForeignKey(
                name: "FK_chat_messages_chat_messages_parent_question_id",
                table: "chat_messages",
                column: "parent_question_id",
                principalTable: "chat_messages",
                principalColumn: "id",
                onDelete: ReferentialAction.SetNull);
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropForeignKey(
                name: "FK_chat_messages_chat_messages_parent_question_id",
                table: "chat_messages");

            migrationBuilder.DropIndex(
                name: "IX_chat_messages_parent_question_id_answer_version",
                table: "chat_messages");

            migrationBuilder.DropIndex(
                name: "IX_chat_messages_parent_question_id",
                table: "chat_messages");

            migrationBuilder.DropColumn(
                name: "parent_question_id",
                table: "chat_messages");

            migrationBuilder.DropColumn(
                name: "answer_version",
                table: "chat_messages");

            migrationBuilder.DropColumn(
                name: "is_current",
                table: "chat_messages");

        }
    }
}
