// Project Amanda -- avatar bridge module.

using UnrealBuildTool;

public class AmandaBridge : ModuleRules
{
	public AmandaBridge(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = ModuleRules.PCHUsageMode.UseExplicitOrSharedPCHs;

		PublicDependencyModuleNames.AddRange(new string[]
		{
			"Core",
			"CoreUObject",
			"Engine",
		});

		PrivateDependencyModuleNames.AddRange(new string[]
		{
			// The engine's WebSocket client. C++ only -- there are no Blueprint
			// nodes for IWebSocket, which is the whole reason this module exists.
			"WebSockets",
			"Json",
		});
	}
}
