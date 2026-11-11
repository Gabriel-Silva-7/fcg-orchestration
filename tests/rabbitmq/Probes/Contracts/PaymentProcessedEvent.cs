using System.Text.Json.Serialization;

namespace FCG.Contracts;

/// <summary>The recorded payment outcome; equality covers every business field.</summary>
[JsonConverter(typeof(PaymentProcessedEventConverter))]
public sealed record PaymentProcessedEvent
{
    public Guid OrderId { get; }
    public Guid UserId { get; }
    public Guid GameId { get; }
    public decimal Price { get; }
    public string Status { get; }
    public string? Reason { get; }
    public DateTimeOffset ProcessedAtUtc { get; }

    public PaymentProcessedEvent(Guid orderId, Guid userId, Guid gameId, decimal price, string status, string? reason, DateTimeOffset processedAtUtc)
    {
        OrderId = PurchaseValues.Id(orderId, nameof(orderId));
        UserId = PurchaseValues.Id(userId, nameof(userId));
        GameId = PurchaseValues.Id(gameId, nameof(gameId));
        Price = PurchaseValues.Price(price);
        PurchaseValues.Result(status, reason);
        Status = status;
        Reason = reason;
        ProcessedAtUtc = PurchaseValues.Instant(processedAtUtc);
    }
}
