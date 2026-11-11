using FCG.Infrastructure.RabbitMq;

try
{
    var settings = BootstrapSettings.FromEnvironment();
    using var timeout = new CancellationTokenSource(settings.Timeout);
    using var api = new ManagementApi(settings, timeout.Token);
    await api.WaitForBroker();
    var reconciler = new TopologyReconciler(api, settings);
    await reconciler.Run();
    Console.WriteLine("rabbitmq bootstrap ready");
    return 0;
}
catch (BootstrapFailure failure)
{
    Console.Error.WriteLine($"{failure.Code}: {failure.Resource}");
    return 1;
}
catch (OperationCanceledException)
{
    Console.Error.WriteLine("bootstrap_timeout");
    return 1;
}
catch (Exception)
{
    // Http/JSON/provider exceptions may carry URLs or response bodies. Never print them.
    Console.Error.WriteLine("bootstrap_failed");
    return 1;
}
