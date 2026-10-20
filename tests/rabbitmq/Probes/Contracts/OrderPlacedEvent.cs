using System.Text.Json.Serialization;

namespace FCG.Contracts;

/// <summary>Immutable BRL order snapshot. Transport MessageId is deliberately separate.</summary>
[JsonConverter(typeof(OrderPlacedEventConverter))]
public sealed record OrderPlacedEvent
{
    public Guid OrderId { get; }
    public Guid UserId { get; }
    public Guid GameId { get; }
    public decimal Price { get; }
    public DateTimeOffset PlacedAtUtc { get; }

    public OrderPlacedEvent(Guid orderId, Guid userId, Guid gameId, decimal price, DateTimeOffset placedAtUtc)
    {
        OrderId = PurchaseValues.Id(orderId, nameof(orderId));
        UserId = PurchaseValues.Id(userId, nameof(userId));
        GameId = PurchaseValues.Id(gameId, nameof(gameId));
        Price = PurchaseValues.Price(price);
        PlacedAtUtc = PurchaseValues.Instant(placedAtUtc);
    }
}
