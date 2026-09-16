using UnrealBuildTool;
public class OpenModelBuilder : ModuleRules
{
    public OpenModelBuilder(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;
        PrivateIncludePaths.Add(System.IO.Path.Combine(EngineDirectory, "Source/Editor/UMGEditor/Private"));
        PublicDependencyModuleNames.AddRange(new string[] {"Core", "CoreUObject", "Engine", "DesktopPlatform"});
        PrivateDependencyModuleNames.AddRange(new string[] {"UnrealEd", "AssetTools", "AssetRegistry", "BlueprintGraph", "Kismet", "KismetCompiler", "UMG", "UMGEditor", "Slate", "SlateCore", "InputCore", "Json", "JsonUtilities", "PropertyEditor"});
    }
}
