using System.Collections.Concurrent;
using System.Text.Json;
using System.Text.Json.Serialization;
using FCG.Contracts;
using MassTransit;

internal static class MassTransitProbe
{
    private static readonly Guid Order = Guid.Parse("11111111-1111-1111-1111-111111111111");
    private static readonly Guid User = Guid.Parse("22222222-2222-2222-2222-222222222222");
    private static readonly Guid Game = Guid.Parse("33333333-3333-3333-3333-333333333333");
    private static readonly Guid Message = Guid.Parse("44444444-4444-4444-4444-444444444444");
    private static readonly DateTimeOffset Placed = DateTimeOffset.Parse("2026-10-19T12:34:56.123456Z");
    private static readonly DateTimeOffset Processed = DateTimeOffset.Parse("2026-10-19T12:34:58.123456Z");
    private static readonly JsonSerializerOptions Json = new(JsonSerializerDefaults.Web) { Converters = { new JsonStringEnumConverter() } };
    public static async Task<object> Run(CancellationToken ct)
    {
        var settings = ConnectionSettings.Read();
        var created = new UserCreatedEvent
        {
            EventId = Message, UserId = User, EventVersion = 1, OccurredAtUtc = Placed, CreatedAtUtc = Placed,
        Name = "Contract User", Email = "user@example.test", Role = IdentityRole.User, State = IdentityState.Active
        };
        var changed = new UserStateChangedEvent
        {
            EventId = Message, UserId = User, EventVersion = 1, OccurredAtUtc = Placed, CreatedAtUtc = Placed,
        Name = "Contract User", Email = "user@example.test", Role = IdentityRole.User, State = IdentityState.Active
        };
        var snapshot = new UserStateSnapshotEvent
        {
            EventId = Message, UserId = User, EventVersion = 1, OccurredAtUtc = Placed, CreatedAtUtc = Placed,
        Name = "Contract User", Email = "user@example.test", Role = IdentityRole.User, State = IdentityState.Active
        };
        var order = new OrderPlacedEvent(Order, User, Game, 44.93m, Placed);
        var payment = new PaymentProcessedEvent(Order, User, Game, 44.93m, "Approved", null, Processed);
        var expected = new Dictionary<Type, string>
        {
            [typeof(UserCreatedEvent)] = JsonSerializer.Serialize(created, Json),
            [typeof(UserStateChangedEvent)] = JsonSerializer.Serialize(changed, Json),
            [typeof(UserStateSnapshotEvent)] = JsonSerializer.Serialize(snapshot, Json),
            [typeof(OrderPlacedEvent)] = JsonSerializer.Serialize(order, Json),
            [typeof(PaymentProcessedEvent)] = JsonSerializer.Serialize(payment, Json)
        };
        var delivered = new ConcurrentDictionary<string, byte>();
        var urns = new ConcurrentDictionary<string, byte>();
        var complete = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
        Task Capture<T>(ConsumeContext<T> context, string endpoint) where T : class
        {
            try
            {
                var urn = "urn:message:FCG.Contracts:" + typeof(T).Name;
                if (context.MessageId != Message || context.CorrelationId != Order
                    || !context.SupportedMessageTypes.Contains(urn)
                    || JsonSerializer.Serialize(context.Message, Json) != expected[typeof(T)])
                    throw new InvalidOperationException("contract_or_correlation_changed");
                urns.TryAdd(urn, 0);
                delivered.TryAdd(endpoint + "/" + typeof(T).Name, 0);
                if (delivered.Count == 9) complete.TrySetResult();
            }
            catch (Exception error) { complete.TrySetException(error); }
            return Task.CompletedTask;
        }
        var bus = Bus.Factory.CreateUsingRabbitMq(cfg =>
        {
            cfg.Host(settings.Host, settings.Port, settings.Vhost, host =>
            {
                host.Username(settings.Username); host.Password(settings.Password); host.PublisherConfirmation = true;
            });
            cfg.ConfigureJsonSerializerOptions(options => { options.Converters.Add(new JsonStringEnumConverter()); return options; });
            foreach (var endpoint in new[] { "fcg-catalog-identity", "fcg-notifications-identity" })
                cfg.ReceiveEndpoint(endpoint, e =>
                {
                    e.Handler<UserCreatedEvent>(c => Capture(c, endpoint));
                    e.Handler<UserStateChangedEvent>(c => Capture(c, endpoint));
                    e.Handler<UserStateSnapshotEvent>(c => Capture(c, endpoint));
                });
            cfg.ReceiveEndpoint("fcg-payments-orders", e => e.Handler<OrderPlacedEvent>(c => Capture(c, "fcg-payments-orders")));
            cfg.ReceiveEndpoint("fcg-catalog-payments", e => e.Handler<PaymentProcessedEvent>(c => Capture(c, "fcg-catalog-payments")));
            cfg.ReceiveEndpoint("fcg-notifications-payments", e => e.Handler<PaymentProcessedEvent>(c => Capture(c, "fcg-notifications-payments")));
        });
        try
        {
            await bus.StartAsync(ct);
            void Set<T>(PublishContext<T> context) where T : class { context.MessageId = Message; context.CorrelationId = Order; }
            await bus.Publish(created, Set, ct);
            await bus.Publish(changed, Set, ct);
            await bus.Publish(snapshot, Set, ct);
            await bus.Publish(order, Set, ct);
            await bus.Publish(payment, Set, ct);
            await complete.Task.WaitAsync(ct);
            var names = new[] { "UserCreatedEvent", "UserStateChangedEvent", "UserStateSnapshotEvent", "OrderPlacedEvent", "PaymentProcessedEvent" };
            return new { delivered = delivered.Count, eventUrns = names.Select(n => "urn:message:FCG.Contracts:" + n).Where(urns.ContainsKey).ToArray(),
                payloadAndCorrelationVerified = true };
        }
        finally
        {
            using var stop = new CancellationTokenSource(TimeSpan.FromSeconds(10));
            await bus.StopAsync(stop.Token);
        }
    }
}
