using System.Security.Cryptography;
using System.Text;
using System.Text.Json.Nodes;

namespace FCG.Infrastructure.RabbitMq;

internal sealed class TopologyReconciler(ManagementApi api, BootstrapSettings settings)
{
    private static string Escape(string value) => Uri.EscapeDataString(value);
    private static string VhostPath => Escape(BrokerTopology.Vhost);
    private sealed record Creation(string Path, object Body, bool IsBinding = false);
    private static readonly object EmptyArguments = new Dictionary<string, object>();
    private static void Require(bool valid, string resource)
    {
        if (!valid) throw new BootstrapFailure("topology_conflict", resource);
    }
    private static bool Empty(JsonNode? node) => node is JsonObject obj && obj.Count == 0;

    private static bool ClassicArguments(JsonNode? node) => Empty(node)
        || node is JsonObject obj && obj.Count == 1 && obj["x-queue-type"]?.GetValue<string>() == "classic";

    public async Task Run()
    {
        // Read and validate ALL existing managed resources before applying any change.
        var missing = await Inspect();
        foreach (var resource in missing)
            if (resource.IsBinding) await api.Post(resource.Path, resource.Body);
            else await api.Put(resource.Path, resource.Body);
        Require((await Inspect()).Count == 0, "readback_incomplete");
    }

    private async Task<List<Creation>> Inspect()
    {
        var missing = new List<Creation>();
        var vhost = await api.Get($"/api/vhosts/{VhostPath}");
        if (vhost is null) missing.Add(new($"/api/vhosts/{VhostPath}", new { default_queue_type = "classic" }));
        else Require(vhost["default_queue_type"]?.GetValue<string>() == "classic", "vhost.default_queue_type");
        foreach (var app in settings.Applications) await InspectUser(app, missing);
        foreach (var name in BrokerTopology.Exchanges)
        {
            var path = $"/api/exchanges/{VhostPath}/{Escape(name)}";
            var exchange = await api.Get(path);
            if (exchange is null) missing.Add(new(path, new { type = "fanout", durable = true, auto_delete = false, @internal = false, arguments = EmptyArguments }));
            else Require(exchange["type"]?.GetValue<string>() == "fanout" && exchange["durable"]?.GetValue<bool>() == true
                && exchange["auto_delete"]?.GetValue<bool>() == false && exchange["internal"]?.GetValue<bool>() == false
                && Empty(exchange["arguments"]), name + ".exchange");
        }
        foreach (var name in BrokerTopology.Endpoints)
        {
            var path = $"/api/queues/{VhostPath}/{Escape(name)}";
            var queue = await api.Get(path);
            if (queue is null) missing.Add(new(path, new { durable = true, auto_delete = false, arguments = EmptyArguments }));
            else Require(queue["type"]?.GetValue<string>() == "classic" && queue["durable"]?.GetValue<bool>() == true
                && queue["auto_delete"]?.GetValue<bool>() == false && queue["exclusive"]?.GetValue<bool>() == false
                && ClassicArguments(queue["arguments"]), name + ".queue");
        }
        var desired = BrokerTopology.Bindings().ToHashSet();
        var found = new HashSet<Binding>();
        foreach (var source in BrokerTopology.Exchanges)
        {
            var bindings = await api.Get($"/api/exchanges/{VhostPath}/{Escape(source)}/bindings/source") as JsonArray;
            if (bindings is null) continue;
            foreach (var edge in bindings)
            {
                var binding = new Binding(source, edge!["destination"]!.GetValue<string>(), edge["destination_type"]!.GetValue<string>());
                Require(desired.Contains(binding) && edge["routing_key"]?.GetValue<string>() == "" && Empty(edge["arguments"]), source + ".binding");
                found.Add(binding);
            }
        }
        foreach (var edge in desired.Except(found))
            missing.Add(new($"/api/bindings/{VhostPath}/e/{Escape(edge.Source)}/{(edge.DestinationType == "queue" ? "q" : "e")}/{Escape(edge.Destination)}",
                new { routing_key = "", arguments = EmptyArguments }, true));
        return missing;
    }

    private async Task InspectUser(ApplicationCredential app, List<Creation> missing)
    {
        var name = Escape(app.Username);
        var user = await api.Get($"/api/users/{name}");
        if (user is null) missing.Add(new($"/api/users/{name}", new { password = app.Password, tags = Array.Empty<string>() }));
        else
        {
            var tags = user["tags"];
            Require(tags is JsonArray array ? array.Count == 0 : tags?.GetValue<string>() == "", app.Username + ".tags");
            Require(PasswordMatches(user, app.Password), app.Username + ".credential");
        }
        var permissions = await api.Get($"/api/users/{name}/permissions") as JsonArray;
        if (permissions is not null)
            foreach (var permission in permissions)
                Require(permission!["vhost"]?.GetValue<string>() == BrokerTopology.Vhost, app.Username + ".vhost");
        var path = $"/api/permissions/{VhostPath}/{name}";
        var own = await api.Get(path);
        if (own is null) missing.Add(new(path, new { configure = ".*", write = ".*", read = ".*" }));
        else Require(own["configure"]?.GetValue<string>() == ".*" && own["write"]?.GetValue<string>() == ".*" && own["read"]?.GetValue<string>() == ".*", app.Username + ".permissions");
    }
    private static bool PasswordMatches(JsonNode user, string password)
    {
        var algorithm = user["hashing_algorithm"]?.GetValue<string>();
        if (algorithm is not ("rabbit_password_hashing_sha256" or "rabbit_password_hashing_sha512")) return false;
        var hash = Convert.FromBase64String(user["password_hash"]!.GetValue<string>());
        if (hash.Length != (algorithm.EndsWith("sha256", StringComparison.Ordinal) ? 36 : 68)) return false;
        var salted = hash[..4].Concat(Encoding.UTF8.GetBytes(password)).ToArray();
        var actual = algorithm.EndsWith("sha256", StringComparison.Ordinal) ? SHA256.HashData(salted) : SHA512.HashData(salted);
        return CryptographicOperations.FixedTimeEquals(hash.AsSpan(4), actual);
    }
}
