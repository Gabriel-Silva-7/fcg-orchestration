using System.Globalization;
using System.Text.RegularExpressions;

namespace FCG.Infrastructure.RabbitMq;

internal sealed record ApplicationCredential(string Username, string Password);
internal sealed record BootstrapSettings(Uri Origin, string Admin, string Password, TimeSpan Timeout,
    IReadOnlyList<ApplicationCredential> Applications)
{
    public static BootstrapSettings FromEnvironment()
    {
        static string Required(string name) => Environment.GetEnvironmentVariable(name) is { Length: > 0 } value
            ? value : throw new BootstrapFailure("configuration_invalid", name);
        static string Username(string name)
        {
            var value = Required(name);
            if (!Regex.IsMatch(value, "\\A[a-zA-Z0-9_.-]+\\z", RegexOptions.CultureInvariant))
                throw new BootstrapFailure("configuration_invalid", name);
            return value;
        }
        var admin = Username("RABBITMQ_BOOTSTRAP_USER");
        var password = Required("RABBITMQ_BOOTSTRAP_PASSWORD");
        var origin = Environment.GetEnvironmentVariable("RABBITMQ_MANAGEMENT_URL") ?? "http://rabbitmq:15672";
        if (!Uri.TryCreate(origin, UriKind.Absolute, out var uri) || uri.Scheme is not ("http" or "https")
            || uri.UserInfo.Length != 0 || uri.Query.Length != 0 || uri.Fragment.Length != 0 || uri.AbsolutePath != "/")
            throw new BootstrapFailure("configuration_invalid", "RABBITMQ_MANAGEMENT_URL");
        var rawTimeout = Environment.GetEnvironmentVariable("RABBITMQ_BOOTSTRAP_TIMEOUT_SECONDS") ?? "60";
        if (!int.TryParse(rawTimeout, NumberStyles.None, CultureInfo.InvariantCulture, out var seconds) || seconds is < 1 or > 300)
            throw new BootstrapFailure("configuration_invalid", "RABBITMQ_BOOTSTRAP_TIMEOUT_SECONDS");
        var names = new HashSet<string>(StringComparer.Ordinal) { admin };
        var apps = new List<ApplicationCredential>();
        foreach (var owner in new[] { "USERS", "CATALOG", "PAYMENTS", "NOTIFICATIONS" })
        {
            var username = Username($"FCG_{owner}_RABBITMQ_USER");
            if (!names.Add(username)) throw new BootstrapFailure("configuration_invalid", "distinct_usernames");
            apps.Add(new(username, Required($"FCG_{owner}_RABBITMQ_PASSWORD")));
        }
        return new(uri, admin, password, TimeSpan.FromSeconds(seconds), apps);
    }
}

internal sealed class BootstrapFailure(string code, string resource) : Exception
{
    public string Code { get; } = code;
    public string Resource { get; } = resource;
}
