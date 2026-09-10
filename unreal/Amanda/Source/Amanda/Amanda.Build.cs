// Project Amanda -- primary game module.
//
// Deliberately almost empty. The build plan's rule is Blueprint first, C++ only
// where justified, and the one place that points at C++ is the WebSocket bridge
// -- which lives in its own plugin. This module exists because a project needs
// a build pipeline to compile that plugin with.

using UnrealBuildTool;

public class Amanda : ModuleRules
{
	public Amanda(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;

		PublicDependencyModuleNames.AddRange(new string[]
		{
			"Core",
			"CoreUObject",
			"Engine",
			"InputCore",
		});
	}
}
