// Project Amanda -- editor target.

using UnrealBuildTool;

public class AmandaEditorTarget : TargetRules
{
	public AmandaEditorTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Editor;
		DefaultBuildSettings = BuildSettingsVersion.Latest;
		IncludeOrderVersion = EngineIncludeOrderVersion.Latest;
		ExtraModuleNames.Add("Amanda");
	}
}
