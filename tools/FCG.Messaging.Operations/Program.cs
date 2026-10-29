using System.Globalization;
using System.Text.Json;
using RabbitMQ.Client;
using RabbitMQ.Client.Exceptions;

var selection = RedriveSelection.Parse(args);
if (selection is null)
{
    Console.Error.WriteLine("Usage: error-redrive --queue QUEUE --message-id GUID [--scan-limit 1..100] [--dry-run | --apply]");
    return 2;
}
try
{
    using var deadline = new CancellationTokenSource(TimeSpan.FromSeconds(30));
    var result = await ErrorRedrive.RunAsync(selection, deadline.Token);
    Console.WriteLine(JsonSerializer.Serialize(result, new JsonSerializerOptions(JsonSerializerDefaults.Web)));
    return result.Found ? 0 : 3;
}
catch (Exception error)
{
    // Broker exceptions can contain credentials, endpoints and private message headers.
    Console.Error.WriteLine("Error redrive failed: " + error.GetType().Name);
    return 1;
}

internal sealed record RedriveSelection(string Queue, Guid MessageId, bool Apply, int Limit)
{
    internal static readonly HashSet<string> Queues = new(StringComparer.Ordinal)
    { "fcg-catalog-identity", "fcg-notifications-identity", "fcg-payments-orders", "fcg-catalog-payments", "fcg-notifications-payments" };
    public static RedriveSelection? Parse(string[] args)
    {
        if (args.Length == 0 || args[0] != "error-redrive") return null;
        string? queue = null; Guid? id = null; var apply = false; var limit = 100;
        var seen = new HashSet<string>(StringComparer.Ordinal);
        for (var i = 1; i < args.Length; i++)
        {
            var flag = args[i]; if (!seen.Add(flag)) return null;
            if (flag is "--apply" or "--dry-run") apply = flag == "--apply";
            else if (flag == "--queue")
            { if (++i >= args.Length || !Queues.Contains(args[i])) return null; queue = args[i]; }
            else if (flag == "--message-id")
            { if (++i >= args.Length || !Guid.TryParse(args[i], out var parsed) || parsed == Guid.Empty) return null; id = parsed; }
            else if (flag == "--scan-limit")
            { if (++i >= args.Length || !int.TryParse(args[i], NumberStyles.None, CultureInfo.InvariantCulture, out limit) || limit is < 1 or > 100) return null; }
            else return null;
        }
        if (queue is null || id is null || seen.Contains("--apply") && seen.Contains("--dry-run")) return null;
        return new(queue, id.Value, apply, limit);
    }
}

internal sealed record RedriveReport(string SourceQueue, string TargetQueue, Guid MessageId, bool Found, bool DryRun, bool Confirmed, int Examined);

internal static class ErrorRedrive
{
    public static async Task<RedriveReport> RunAsync(RedriveSelection selection, CancellationToken ct)
    {
        static string Required(string key) => Environment.GetEnvironmentVariable(key) is { Length: > 0 } value ? value : throw new InvalidOperationException("Missing broker configuration.");
        var rawPort = Environment.GetEnvironmentVariable("AMQP_PORT") ?? "5672";
        if (!int.TryParse(rawPort, NumberStyles.None, CultureInfo.InvariantCulture, out var port) || port is < 1 or > 65535)
            throw new InvalidOperationException("Invalid broker port.");
        var factory = new ConnectionFactory
        {
            HostName = Environment.GetEnvironmentVariable("AMQP_HOST") ?? "localhost", Port = port,
            VirtualHost = Environment.GetEnvironmentVariable("AMQP_VHOST") ?? "/fcg",
            UserName = Required("AMQP_USERNAME"), Password = Required("AMQP_PASSWORD"),
            AutomaticRecoveryEnabled = false, RequestedConnectionTimeout = TimeSpan.FromSeconds(5)
        };
        await using var connection = await factory.CreateConnectionAsync(ct);
        await using var channel = await connection.CreateChannelAsync(new CreateChannelOptions(true, true), ct);
        var source = selection.Queue + "_error";
        // Never create or repair queues. A missing destination must leave the original message untouched.
        await channel.QueueDeclarePassiveAsync(selection.Queue, ct);
        try { await channel.QueueDeclarePassiveAsync(source, ct); }
        catch (OperationInterruptedException error) when (error.ShutdownReason?.ReplyCode == 404)
        { return new(source, selection.Queue, selection.MessageId, false, !selection.Apply, false, 0); }
        var held = new List<ulong>(); BasicGetResult? selected = null; var examined = 0;
        while (examined < selection.Limit)
        {
            var next = await channel.BasicGetAsync(source, false, ct); if (next is null) break;
            examined++;
            if (Guid.TryParse(next.BasicProperties.MessageId, out var id) && id == selection.MessageId)
            { selected = next; break; }
            held.Add(next.DeliveryTag);
        }
        if (selected is null)
        {
            foreach (var tag in held) await channel.BasicNackAsync(tag, false, true, ct);
            return new(source, selection.Queue, selection.MessageId, false, !selection.Apply, false, examined);
        }
        if (!selection.Apply)
        {
            held.Add(selected.DeliveryTag);
            foreach (var tag in held) await channel.BasicNackAsync(tag, false, true, ct);
            return new(source, selection.Queue, selection.MessageId, true, true, false, examined);
        }
        if (!selected.BasicProperties.Persistent) throw new InvalidDataException("Only persistent project messages may be redriven.");
        var properties = new BasicProperties(selected.BasicProperties);
        // Confirmed mandatory direct publication precedes ack. A crash between them may duplicate, never intentionally lose.
        await channel.BasicPublishAsync("", selection.Queue, true, properties, selected.Body, ct);
        await channel.BasicAckAsync(selected.DeliveryTag, false, ct);
        foreach (var tag in held) await channel.BasicNackAsync(tag, false, true, ct);
        return new(source, selection.Queue, selection.MessageId, true, false, true, examined);
    }
}
