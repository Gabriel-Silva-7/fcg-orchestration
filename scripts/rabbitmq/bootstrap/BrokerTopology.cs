namespace FCG.Infrastructure.RabbitMq;

internal sealed record Binding(string Source, string Destination, string DestinationType);
internal static class BrokerTopology
{
    public const string Vhost = "/fcg";
    public static readonly string[] Endpoints = ["fcg-catalog-identity", "fcg-notifications-identity", "fcg-payments-orders", "fcg-catalog-payments", "fcg-notifications-payments"];
    public static readonly string[] IdentityEvents = ["UserCreatedEvent", "UserStateChangedEvent", "UserStateSnapshotEvent"];
    public static readonly string[] Events = [.. IdentityEvents, "OrderPlacedEvent", "PaymentProcessedEvent", "IdentitySnapshot"];
    public static readonly string[] Exchanges = [.. Endpoints, .. Events.Select(EventExchange)];
    public static string EventExchange(string name) => "FCG.Contracts:" + name;
    public static IEnumerable<Binding> Bindings()
    {
        foreach (var endpoint in Endpoints) yield return new(endpoint, endpoint, "queue");
        foreach (var name in IdentityEvents)
        {
            foreach (var endpoint in Endpoints.Take(2)) yield return new(EventExchange(name), endpoint, "exchange");
            yield return new(EventExchange(name), EventExchange("IdentitySnapshot"), "exchange");
        }
        yield return new(EventExchange("OrderPlacedEvent"), Endpoints[2], "exchange");
        yield return new(EventExchange("PaymentProcessedEvent"), Endpoints[3], "exchange");
        yield return new(EventExchange("PaymentProcessedEvent"), Endpoints[4], "exchange");
    }
}
