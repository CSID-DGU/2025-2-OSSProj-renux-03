using AutoMapper;
using Microsoft.EntityFrameworkCore;
using FluentValidation;

using RenuexServer.DbContexts;
using RenuexServer.Dtos.AuthDtos;
using RenuexServer.Middlewares;
using RenuexServer.Validators;
using Microsoft.IdentityModel.Tokens;
using System.IdentityModel.Tokens.Jwt;
using RenuexServer.Apis.Auth;

var builder = WebApplication.CreateBuilder();

builder.Configuration.AddUserSecrets<Program>();
builder.Services.AddDbContext<ServerDbContext>(options =>
{
    options.UseNpgsql(builder.Configuration.GetConnectionString("RenuxServer"));
});
builder.Services.AddAutoMapper(options =>
{
    options.CreateMap<SignupUserDto, Profile>();
});
builder.Services.AddValidatorsFromAssemblyContaining<SignupUserValidator>();
builder.Services.AddValidatorsFromAssemblyContaining<SigninUserValidator>();

builder.Services.AddAuthentication()
    .AddJwtBearer(options =>
    {
        options.TokenValidationParameters = new()
        {
            IssuerSigningKey = new SymmetricSecurityKey(Convert.FromBase64String(builder.Configuration["Jwt:Key"]!)),
            ValidateIssuer = false,
            ValidateAudience = false
        };

        options.Events = new()
        {
            OnMessageReceived = new(context =>
            {
                if (context.Request.Cookies.ContainsKey("renux-server-token"))
                    context.Token = context.Request.Cookies["renux-server-token"];
                return Task.CompletedTask;
            })
        };
    });

builder.Services.AddAuthorization();

var app = builder.Build();

app.UseMiddleware<GlobalExceptionHandlerMiddleware>();

app.UseAuthentication();
app.UseAuthorization();

app.AddAuthApis();

app.Run();