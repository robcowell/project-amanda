// Project Amanda -- game target.

using UnrealBuildTool;

public class AmandaTarget : TargetRules
{
	public AmandaTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Game;
		DefaultBuildSettings = BuildSettingsVersion.Latest;
		IncludeOrderVersion = EngineIncludeOrderVersion.Latest;
		ExtraModuleNames.Add("Amanda");
	}
}
