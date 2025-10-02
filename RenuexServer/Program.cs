using AutoMapper;
using Microsoft.EntityFrameworkCore;
using FluentValidation;

using RenuexServer.DbContexts;
using RenuexServer.Dtos.AuthDtos;
using RenuexServer.Middlewares;
using RenuexServer.Validators;

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

var app = builder.Build();

app.UseMiddleware<GlobalExceptionHandlerMiddleware>();



app.Run();