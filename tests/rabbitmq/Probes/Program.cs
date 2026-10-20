using System.Text.Json;

try
{
    using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(25));
    var command = args.Single();
    var result = command == "masstransit"
        ? await MassTransitProbe.Run(timeout.Token)
        : await AmqpProbe.Run(command, timeout.Token);
    Console.WriteLine(JsonSerializer.Serialize(result));
    return 0;
}
catch (Exception error)
{
    // Exceptions from protocol clients may contain connection/credential information.
    Console.Error.WriteLine("probe_failed: " + error.GetType().Name);
    return 1;
}

internal sealed record ConnectionSettings(string Host, ushort Port, string Vhost, string Username, string Password)
{
    public static ConnectionSettings Read() => new(
        Environment.GetEnvironmentVariable("AMQP_HOST") ?? "127.0.0.1",
        ushort.Parse(Environment.GetEnvironmentVariable("AMQP_PORT") ?? "5672"),
        Environment.GetEnvironmentVariable("AMQP_VHOST") ?? "/fcg",
        Environment.GetEnvironmentVariable("AMQP_USERNAME") ?? throw new InvalidOperationException(),
        Environment.GetEnvironmentVariable("AMQP_PASSWORD") ?? throw new InvalidOperationException());
}
