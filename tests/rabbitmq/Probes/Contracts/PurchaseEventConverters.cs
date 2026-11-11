using System.Text.Json;
using System.Text.Json.Serialization;

namespace FCG.Contracts;

internal static class PurchasePayload
{
    internal static JsonDocument Read(ref Utf8JsonReader reader, string[] required, params string[] optional)
    {
        var document = JsonDocument.ParseValue(ref reader);
        try
        {
            if (document.RootElement.ValueKind != JsonValueKind.Object)
                throw new JsonException("Expected a purchase object.");
            var seen = new HashSet<string>(StringComparer.Ordinal);
            foreach (var field in document.RootElement.EnumerateObject())
                if (!seen.Add(field.Name) || !required.Contains(field.Name) && !optional.Contains(field.Name))
                    throw new JsonException($"Unknown or duplicate purchase field: {field.Name}.");
            if (required.Any(field => !seen.Contains(field)))
                throw new JsonException("Missing required purchase field.");
            return document;
        }
        catch { document.Dispose(); throw; }
    }

    internal static T Value<T>(JsonElement root, string name, JsonSerializerOptions options) =>
        root.GetProperty(name).Deserialize<T>(options)!;

    internal static decimal Price(JsonElement root) =>
        JsonSerializer.Deserialize<decimal>(root.GetProperty("price").GetRawText(), ScalarOptions);

    internal static DateTimeOffset Instant(JsonElement root, string name) =>
        JsonSerializer.Deserialize<DateTimeOffset>(root.GetProperty(name).GetRawText(), ScalarOptions);

    // Local converters do not affect identity contracts or the application's serializer configuration.
    private static readonly JsonSerializerOptions ScalarOptions = new()
    {
        Converters = { new PurchasePriceConverter(), new PurchaseInstantConverter() }
    };

    internal static void Snapshot(Utf8JsonWriter writer, Guid orderId, Guid userId, Guid gameId, decimal price)
    {
        writer.WriteStartObject();
        writer.WriteString("orderId", orderId);
        writer.WriteString("userId", userId);
        writer.WriteString("gameId", gameId);
        writer.WritePropertyName("price");
        JsonSerializer.Serialize(writer, price, ScalarOptions);
    }

    internal static void WriteInstant(Utf8JsonWriter writer, string name, DateTimeOffset value)
    {
        writer.WritePropertyName(name);
        JsonSerializer.Serialize(writer, value, ScalarOptions);
    }
}

internal sealed class OrderPlacedEventConverter : JsonConverter<OrderPlacedEvent>
{
    public override bool HandleNull => true;
    public override OrderPlacedEvent Read(ref Utf8JsonReader reader, Type type, JsonSerializerOptions options)
    {
        using var document = PurchasePayload.Read(ref reader, ["orderId", "userId", "gameId", "price", "placedAtUtc"]);
        var root = document.RootElement;
        return new(PurchasePayload.Value<Guid>(root, "orderId", options), PurchasePayload.Value<Guid>(root, "userId", options),
            PurchasePayload.Value<Guid>(root, "gameId", options), PurchasePayload.Price(root), PurchasePayload.Instant(root, "placedAtUtc"));
    }
    public override void Write(Utf8JsonWriter writer, OrderPlacedEvent value, JsonSerializerOptions options)
    {
        if (value is null) throw new JsonException("A purchase event cannot be null.");
        PurchasePayload.Snapshot(writer, value.OrderId, value.UserId, value.GameId, value.Price);
        PurchasePayload.WriteInstant(writer, "placedAtUtc", value.PlacedAtUtc);
        writer.WriteEndObject();
    }
}

internal sealed class PaymentProcessedEventConverter : JsonConverter<PaymentProcessedEvent>
{
    public override bool HandleNull => true;
    public override PaymentProcessedEvent Read(ref Utf8JsonReader reader, Type type, JsonSerializerOptions options)
    {
        using var document = PurchasePayload.Read(ref reader,
            ["orderId", "userId", "gameId", "price", "status", "processedAtUtc"], "reason");
        var root = document.RootElement;
        return new(PurchasePayload.Value<Guid>(root, "orderId", options), PurchasePayload.Value<Guid>(root, "userId", options),
            PurchasePayload.Value<Guid>(root, "gameId", options), PurchasePayload.Price(root),
            PurchasePayload.Value<string>(root, "status", options),
            root.TryGetProperty("reason", out var reason) ? reason.GetString() : null, PurchasePayload.Instant(root, "processedAtUtc"));
    }
    public override void Write(Utf8JsonWriter writer, PaymentProcessedEvent value, JsonSerializerOptions options)
    {
        if (value is null) throw new JsonException("A purchase event cannot be null.");
        PurchasePayload.Snapshot(writer, value.OrderId, value.UserId, value.GameId, value.Price);
        writer.WriteString("status", value.Status);
        if (value.Reason is not null) writer.WriteString("reason", value.Reason);
        PurchasePayload.WriteInstant(writer, "processedAtUtc", value.ProcessedAtUtc);
        writer.WriteEndObject();
    }
}
