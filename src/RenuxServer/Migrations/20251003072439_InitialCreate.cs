﻿using System;
using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace RenuxServer.Migrations
{
    /// <inheritdoc />
    public partial class InitialCreate : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.CreateTable(
                name: "majors",
                columns: table => new
                {
                    id = table.Column<Guid>(type: "uuid", nullable: false),
                    department_name = table.Column<string>(type: "text", nullable: false)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_majors", x => x.id);
                });

            migrationBuilder.CreateTable(
                name: "roles",
                columns: table => new
                {
                    id = table.Column<Guid>(type: "uuid", nullable: false),
                    role_name = table.Column<string>(type: "text", nullable: false)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_roles", x => x.id);
                });

            migrationBuilder.CreateTable(
                name: "organizations",
                columns: table => new
                {
                    id = table.Column<Guid>(type: "uuid", nullable: false),
                    major_id = table.Column<Guid>(type: "uuid", nullable: false),
                    is_active = table.Column<bool>(type: "boolean", nullable: false),
                    created_time = table.Column<long>(type: "bigint", nullable: false),
                    updated_time = table.Column<long>(type: "bigint", nullable: false)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_organizations", x => x.id);
                    table.ForeignKey(
                        name: "FK_organizations_majors_major_id",
                        column: x => x.major_id,
                        principalTable: "majors",
                        principalColumn: "id",
                        onDelete: ReferentialAction.Cascade);
                });

            migrationBuilder.CreateTable(
                name: "users",
                columns: table => new
                {
                    id = table.Column<Guid>(type: "uuid", nullable: false),
                    user_id = table.Column<string>(type: "text", nullable: false),
                    password = table.Column<string>(type: "text", nullable: false),
                    user_name = table.Column<string>(type: "text", nullable: false),
                    major_id = table.Column<Guid>(type: "uuid", nullable: false),
                    role = table.Column<Guid>(type: "uuid", nullable: false),
                    created_time = table.Column<long>(type: "bigint", nullable: false),
                    updated_time = table.Column<long>(type: "bigint", nullable: false)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_users", x => x.id);
                    table.ForeignKey(
                        name: "FK_users_majors_major_id",
                        column: x => x.major_id,
                        principalTable: "majors",
                        principalColumn: "id",
                        onDelete: ReferentialAction.Cascade);
                    table.ForeignKey(
                        name: "FK_users_roles_role",
                        column: x => x.role,
                        principalTable: "roles",
                        principalColumn: "id",
                        onDelete: ReferentialAction.Cascade);
                });

            migrationBuilder.CreateTable(
                name: "guest",
                columns: table => new
                {
                    id = table.Column<Guid>(type: "uuid", nullable: false),
                    organization_id = table.Column<Guid>(type: "uuid", nullable: false),
                    title = table.Column<string>(type: "text", nullable: false),
                    created_time = table.Column<long>(type: "bigint", nullable: false),
                    updated_time = table.Column<long>(type: "bigint", nullable: false)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_guest", x => x.id);
                    table.ForeignKey(
                        name: "FK_guest_organizations_organization_id",
                        column: x => x.organization_id,
                        principalTable: "organizations",
                        principalColumn: "id",
                        onDelete: ReferentialAction.Cascade);
                });

            migrationBuilder.CreateTable(
                name: "active_chats",
                columns: table => new
                {
                    id = table.Column<Guid>(type: "uuid", nullable: false),
                    user_id = table.Column<Guid>(type: "uuid", nullable: false),
                    organization_id = table.Column<Guid>(type: "uuid", nullable: false),
                    title = table.Column<string>(type: "text", nullable: false),
                    created_time = table.Column<long>(type: "bigint", nullable: false),
                    updated_time = table.Column<long>(type: "bigint", nullable: false)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_active_chats", x => x.id);
                    table.ForeignKey(
                        name: "FK_active_chats_organizations_organization_id",
                        column: x => x.organization_id,
                        principalTable: "organizations",
                        principalColumn: "id",
                        onDelete: ReferentialAction.Cascade);
                    table.ForeignKey(
                        name: "FK_active_chats_users_user_id",
                        column: x => x.user_id,
                        principalTable: "users",
                        principalColumn: "id",
                        onDelete: ReferentialAction.Cascade);
                });

            migrationBuilder.CreateTable(
                name: "chat_messages",
                columns: table => new
                {
                    id = table.Column<Guid>(type: "uuid", nullable: false),
                    chat_id = table.Column<Guid>(type: "uuid", nullable: false),
                    is_ask = table.Column<bool>(type: "boolean", nullable: false),
                    content = table.Column<string>(type: "text", nullable: false),
                    created_time = table.Column<long>(type: "bigint", nullable: false)
                },
                constraints: table =>
                {
                    table.PrimaryKey("PK_chat_messages", x => x.id);
                    table.ForeignKey(
                        name: "FK_chat_messages_active_chats_chat_id",
                        column: x => x.chat_id,
                        principalTable: "active_chats",
                        principalColumn: "id",
                        onDelete: ReferentialAction.Cascade);
                });

            migrationBuilder.CreateIndex(
                name: "IX_active_chats_organization_id",
                table: "active_chats",
                column: "organization_id");

            migrationBuilder.CreateIndex(
                name: "IX_active_chats_user_id",
                table: "active_chats",
                column: "user_id");

            migrationBuilder.CreateIndex(
                name: "IX_chat_messages_chat_id",
                table: "chat_messages",
                column: "chat_id");

            migrationBuilder.CreateIndex(
                name: "IX_chat_messages_created_time",
                table: "chat_messages",
                column: "created_time");

            migrationBuilder.CreateIndex(
                name: "IX_guest_organization_id",
                table: "guest",
                column: "organization_id");

            migrationBuilder.CreateIndex(
                name: "IX_organizations_major_id",
                table: "organizations",
                column: "major_id");

            migrationBuilder.CreateIndex(
                name: "IX_users_major_id",
                table: "users",
                column: "major_id");

            migrationBuilder.CreateIndex(
                name: "IX_users_role",
                table: "users",
                column: "role");

            migrationBuilder.CreateIndex(
                name: "IX_users_user_id",
                table: "users",
                column: "user_id",
                unique: true);
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropTable(
                name: "chat_messages");

            migrationBuilder.DropTable(
                name: "guest");

            migrationBuilder.DropTable(
                name: "active_chats");

            migrationBuilder.DropTable(
                name: "organizations");

            migrationBuilder.DropTable(
                name: "users");

            migrationBuilder.DropTable(
                name: "majors");

            migrationBuilder.DropTable(
                name: "roles");
        }
    }
}
