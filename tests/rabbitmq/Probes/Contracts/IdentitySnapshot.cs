namespace FCG.Contracts;

public enum IdentityRole { User, Administrator }
public enum IdentityState { Active, Blocked, Deleted }

/// <summary>Complete identity state. EventVersion is a durable per-user logical version, never xmin.</summary>
public abstract record IdentitySnapshot
{
    public required Guid EventId { get; init; }
    public required Guid UserId { get; init; }
    public required long EventVersion { get; init; }
    public required DateTimeOffset OccurredAtUtc { get; init; }
    public required DateTimeOffset CreatedAtUtc { get; init; }
    public required string Name { get; init; }
    public required string Email { get; init; }
    public required IdentityRole Role { get; init; }
    public required IdentityState State { get; init; }
}

/// <summary>A registration fact; a delayed copy can still create one welcome intent.</summary>
public sealed record UserCreatedEvent : IdentitySnapshot;
/// <summary>A complete new version after a real contact or access change.</summary>
public sealed record UserStateChangedEvent : IdentitySnapshot;
/// <summary>Recovery of state only; never a registration or welcome intent.</summary>
public sealed record UserStateSnapshotEvent : IdentitySnapshot;
