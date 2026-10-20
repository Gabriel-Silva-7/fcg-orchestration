using RabbitMQ.Client;
using System.Text;

internal static class AmqpProbe
{
    public static async Task<object> Run(string command, CancellationToken ct)
    {
        var settings = ConnectionSettings.Read();
        var factory = new ConnectionFactory
        {
            HostName = settings.Host, Port = settings.Port, VirtualHost = settings.Vhost,
            UserName = settings.Username, Password = settings.Password, AutomaticRecoveryEnabled = false,
            RequestedConnectionTimeout = TimeSpan.FromSeconds(5)
        };
        await using var connection = await factory.CreateConnectionAsync(ct);
        await using var channel = await connection.CreateChannelAsync(new CreateChannelOptions(true, true), ct);
        if (command == "connect")
        {
            await channel.BasicQosAsync(0, 1, false, ct);
            if (!connection.IsOpen || !channel.IsOpen) throw new InvalidOperationException("connection_closed");
            return new { connected = true, vhost = settings.Vhost };
        }
        if (command == "publish-confirm")
        {
            var properties = new BasicProperties
            {
                Persistent = true, ContentType = "application/json",
                MessageId = "44444444-4444-4444-4444-444444444444",
                CorrelationId = "11111111-1111-1111-1111-111111111111"
            };
            await channel.BasicPublishAsync(Environment.GetEnvironmentVariable("EXCHANGE") ?? "FCG.Contracts:PaymentProcessedEvent",
                Environment.GetEnvironmentVariable("ROUTING_KEY") ?? "", true, properties,
                Encoding.UTF8.GetBytes(Environment.GetEnvironmentVariable("MESSAGE_BODY") ?? "marker"), ct);
            // With tracking enabled BasicPublishAsync completes after the broker confirm,
            // and throws for a mandatory return or nack. No HTTP publication is involved.
            return new { protocol = "AMQP 0-9-1", confirmed = true };
        }
        if (command == "counts")
        {
            var counts = new Dictionary<string, uint>();
            foreach (var queue in new[] { "fcg-catalog-identity", "fcg-notifications-identity", "fcg-payments-orders", "fcg-catalog-payments", "fcg-notifications-payments" })
                counts[queue] = (await channel.QueueDeclarePassiveAsync(queue, ct)).MessageCount;
            return new { queues = counts };
        }
        if (command == "get-ack")
        {
            var result = await channel.BasicGetAsync(Environment.GetEnvironmentVariable("QUEUE")!, false, ct)
                ?? throw new InvalidOperationException("queue_empty");
            await channel.BasicAckAsync(result.DeliveryTag, false, ct);
            return new { body = Encoding.UTF8.GetString(result.Body.Span), messageId = result.BasicProperties.MessageId,
                correlationId = result.BasicProperties.CorrelationId, persistent = result.BasicProperties.Persistent };
        }
        throw new ArgumentException("unknown_command");
    }
}
