// Run against the deployed assembly, not a duplicate format table.
using System;
using System.IO;
using System.Reflection;
using System.Runtime.Loader;

if (args.Length != 2) throw new ArgumentException("Expected emulator directory and before/after mode");
string directory = Path.GetFullPath(args[0]);
AssemblyLoadContext.Default.Resolving += (_, name) => {
    string path = Path.Combine(directory, name.Name + ".dll");
    return File.Exists(path) ? AssemblyLoadContext.Default.LoadFromAssemblyPath(path) : null;
};
var assembly = AssemblyLoadContext.Default.LoadFromAssemblyPath(Path.Combine(directory, "Ryujinx.Graphics.Gpu.dll"));
var type = assembly.GetType("Ryujinx.Graphics.Gpu.Image.FormatTable", throwOnError: true)!;
var method = type.GetMethod("TryGetTextureFormat", BindingFlags.Public | BindingFlags.Static)!;
bool shouldRecognize = args[1] == "after";
foreach (var pair in new (uint Alias, uint Existing, string Format)[] {
    (0x2512b, 0x4912b, "S8UintD24Unorm"), (0x2512a, 0x4912a, "X8UintD24Unorm") }) {
    object?[] original = { pair.Existing, false, null };
    if (!(bool)method.Invoke(null, original)!) throw new Exception("Existing depth format regressed");
    object?[] alias = { pair.Alias, false, null };
    bool found = (bool)method.Invoke(null, alias)!;
    if (found != shouldRecognize) throw new Exception($"Unexpected recognition for {pair.Alias:X}: {found}");
    if (found) {
        var info = alias[2]!;
        var infoType = info.GetType();
        foreach (string property in new[] { "Format", "BlockWidth", "BlockHeight", "BytesPerPixel", "Components" }) {
            var p = infoType.GetProperty(property)!;
            if (!Equals(p.GetValue(info), p.GetValue(original[2]))) throw new Exception($"Alias differs: {property}");
        }
        if (infoType.GetProperty("Format")!.GetValue(info)!.ToString() != pair.Format)
            throw new Exception("Incorrect depth/stencil layout");
    }
    Console.WriteLine($"{pair.Alias:X}: recognized={found}, expected={pair.Format}");
}
object?[] invalid = { 0u, false, null };
if ((bool)method.Invoke(null, invalid)!) throw new Exception("Invalid texture format was accepted");
