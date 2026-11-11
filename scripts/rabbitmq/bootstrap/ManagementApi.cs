using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text;
using System.Text.Json.Nodes;

namespace FCG.Infrastructure.RabbitMq;

internal sealed class ManagementApi : IDisposable
{
    private readonly HttpClient client;
    private readonly CancellationToken cancellation;
    public ManagementApi(BootstrapSettings settings, CancellationToken cancellation)
    {
        this.cancellation = cancellation;
        client = new(new HttpClientHandler { AllowAutoRedirect = false }) { BaseAddress = settings.Origin, Timeout = Timeout.InfiniteTimeSpan };
        client.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Basic",
            Convert.ToBase64String(Encoding.UTF8.GetBytes(settings.Admin + ":" + settings.Password)));
    }
    public async Task WaitForBroker() => _ = await Get("/api/overview");
    public Task<JsonNode?> Get(string path) => Send(HttpMethod.Get, path, null);
    public Task<JsonNode?> Put(string path, object value) => Send(HttpMethod.Put, path, value);
    public Task<JsonNode?> Post(string path, object value) => Send(HttpMethod.Post, path, value);
    private async Task<JsonNode?> Send(HttpMethod method, string path, object? body)
    {
        while (true)
        {
            cancellation.ThrowIfCancellationRequested();
            try
            {
                using var request = new HttpRequestMessage(method, path);
                if (body is not null) request.Content = JsonContent.Create(body);
                using var response = await client.SendAsync(request, cancellation);
                if (response.StatusCode is HttpStatusCode.Unauthorized or HttpStatusCode.Forbidden)
                    throw new BootstrapFailure("authentication_failed", "management_admin");
                if (response.StatusCode == HttpStatusCode.NotFound && method == HttpMethod.Get) return null;
                if ((int)response.StatusCode >= 500)
                {
                    await Task.Delay(200, cancellation);
                    continue;
                }
                if (!response.IsSuccessStatusCode) throw new BootstrapFailure("bootstrap_failed", path);
                var bytes = await response.Content.ReadAsByteArrayAsync(cancellation);
                return bytes.Length == 0 ? null : JsonNode.Parse(bytes);
            }
            catch (HttpRequestException) { await Task.Delay(200, cancellation); }
        }
    }
    public void Dispose() => client.Dispose();
}
