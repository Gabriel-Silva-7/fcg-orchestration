using System.Globalization;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace FCG.Contracts;

internal static class PurchaseValues
{
    internal static Guid Id(Guid value, string name) => value != Guid.Empty
        ? value : throw new ArgumentException("A purchase identifier cannot be empty.", name);

    internal static decimal Price(decimal value) => value >= 0m && value <= 9999999999999999.99m && decimal.Round(value, 2) == value
        ? value : throw new ArgumentOutOfRangeException(nameof(value), "Price must fit BRL numeric(18,2), without rounding.");

    internal static DateTimeOffset Instant(DateTimeOffset value) => value.Offset == TimeSpan.Zero && value.Ticks % 10 == 0
        ? value : throw new ArgumentException("Purchase timestamps must be UTC with microsecond precision.", nameof(value));

    internal static void Result(string status, string? reason)
    {
        if (!(status == "Approved" && reason is null || status == "Rejected" && reason == "AmountAboveLimit"))
            throw new ArgumentException("Expected Approved without a reason, or Rejected with AmountAboveLimit.");
    }
}

internal sealed class PurchaseInstantConverter : JsonConverter<DateTimeOffset>
{
    private const string Format = "yyyy-MM-dd'T'HH:mm:ss.ffffff'Z'";
    public override DateTimeOffset Read(ref Utf8JsonReader reader, Type type, JsonSerializerOptions options)
    {
        if (reader.TokenType != JsonTokenType.String || !DateTimeOffset.TryParseExact(reader.GetString(), Format,
                CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal, out var value))
            throw new JsonException("Expected UTC yyyy-MM-ddTHH:mm:ss.ffffffZ.");
        return value;
    }
    public override void Write(Utf8JsonWriter writer, DateTimeOffset value, JsonSerializerOptions options) =>
        writer.WriteStringValue(PurchaseValues.Instant(value).ToString(Format, CultureInfo.InvariantCulture));
}

internal sealed class PurchasePriceConverter : JsonConverter<decimal>
{
    public override decimal Read(ref Utf8JsonReader reader, Type type, JsonSerializerOptions options)
    {
        if (reader.TokenType != JsonTokenType.Number)
            throw new JsonException("Expected a decimal BRL number.");
        using var document = JsonDocument.ParseValue(ref reader);
        var raw = document.RootElement.GetRawText();
        var exponentAt = raw.IndexOfAny(['e', 'E']);
        var mantissa = exponentAt < 0 ? raw : raw[..exponentAt];
        var pointAt = mantissa.IndexOf('.');
        var fractionalDigits = pointAt < 0 ? 0 : mantissa.Length - pointAt - 1;
        var digits = mantissa.Replace(".", "", StringComparison.Ordinal).TrimStart('-').TrimStart('0');
        if (digits.Length == 0) return 0m;
        var significant = digits.TrimEnd('0');
        var removedZeros = digits.Length - significant.Length;
        if (mantissa[0] == '-' || significant.Length > 18 ||
            !int.TryParse(exponentAt < 0 ? "0" : raw[(exponentAt + 1)..], NumberStyles.AllowLeadingSign,
                CultureInfo.InvariantCulture, out var exponent))
            throw new JsonException("Price is outside BRL numeric(18,2).");
        // Evaluate exact integer cents before decimal parsing can round a long JSON number.
        var power = 2L - fractionalDigits + removedZeros + exponent;
        if (power < 0 || power > 18 || significant.Length + power > 18)
            throw new JsonException("Price cannot be represented as exact BRL cents.");
        var cents = decimal.Parse(significant, CultureInfo.InvariantCulture);
        for (var i = 0; i < power; i++) cents *= 10m;
        return PurchaseValues.Price(cents / 100m);
    }
    public override void Write(Utf8JsonWriter writer, decimal value, JsonSerializerOptions options) =>
        writer.WriteRawValue(PurchaseValues.Price(value).ToString("0.00", CultureInfo.InvariantCulture));
}
