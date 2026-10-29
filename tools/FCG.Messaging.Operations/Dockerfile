FROM mcr.microsoft.com/dotnet/sdk:10.0.400@sha256:4beef5b8919dcaa2dc924233bd069257e883cc7a061e09088a97d152d6a48510 AS build
WORKDIR /src
COPY global.json ./
COPY tools/FCG.Messaging.Operations/ tools/FCG.Messaging.Operations/
RUN dotnet restore tools/FCG.Messaging.Operations/FCG.Messaging.Operations.csproj --locked-mode
RUN dotnet publish tools/FCG.Messaging.Operations/FCG.Messaging.Operations.csproj -c Release --no-restore -o /out

FROM mcr.microsoft.com/dotnet/aspnet:10.0.11@sha256:011bb5f30180717b1c8b65822ff2c99bcb96bc65af0164589751b83c7b4949f7
WORKDIR /app
USER app
COPY --from=build /out/ ./
ENTRYPOINT ["dotnet", "FCG.Messaging.Operations.dll"]
