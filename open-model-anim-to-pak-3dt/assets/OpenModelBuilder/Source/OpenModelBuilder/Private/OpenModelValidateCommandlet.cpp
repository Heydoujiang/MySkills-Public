#include "OpenModelBuildCommandlet.h"
#include "Editor.h"
#include "Engine/Blueprint.h"
#include "Engine/SkeletalMesh.h"
#include "Components/SkeletalMeshComponent.h"
#include "Animation/AnimSequence.h"
#include "Animation/AnimSingleNodeInstance.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "UObject/UnrealType.h"
#include "HAL/PlatformTime.h"
#include "UObject/StructOnScope.h"
#include "K2Node_CallFunction.h"
#include "K2Node_Event.h"
#include "K2Node_InputKey.h"
#include "K2Node_VariableGet.h"
#include "AssetRegistryModule.h"

UOpenModelValidateCommandlet::UOpenModelValidateCommandlet()
{
    IsClient = false; IsEditor = true; IsServer = false; LogToConsole = true;
}

int32 UOpenModelValidateCommandlet::Main(const FString& Params)
{
    FString Root = TEXT("/Game/OpenModel"), SamplesPath, ReportPath;
    FParse::Value(*Params, TEXT("Root="), Root);
    FParse::Value(*Params, TEXT("Samples="), SamplesPath);
    FParse::Value(*Params, TEXT("Report="), ReportPath);
    const bool NoUMG=FParse::Param(*Params,TEXT("NoUMG"));
    FString BPName=NoUMG?TEXT("BP_ExplodedAssembly_NoUMG"):TEXT("BP_ExplodedAssembly");
    FParse::Value(*Params,TEXT("BPName="),BPName);
    auto* BP = LoadObject<UBlueprint>(nullptr, *(Root + TEXT("/")+BPName+TEXT(".")+BPName));
    if (!BP || !BP->GeneratedClass) return 2;
    TGuardValue<bool> ScriptExecutionGuard(GAllowActorScriptExecutionInEditor, true);
    UWorld* World = GEditor->NewMap();
    AActor* Actor = World->SpawnActor<AActor>(BP->GeneratedClass);
    auto* Mesh = Actor->FindComponentByClass<USkeletalMeshComponent>();
    if (!Mesh || !Mesh->SkeletalMesh) return 3;
    Actor->ProcessEvent(Actor->FindFunctionChecked(TEXT("ReceiveBeginPlay")), nullptr);
    auto ReadFloat = [Actor](const TCHAR* Name) {
        auto* Prop = FindFProperty<FFloatProperty>(Actor->GetClass(), Name); check(Prop);
        return Prop->GetPropertyValue_InContainer(Actor);
    };
    auto ReadBool = [Actor](const TCHAR* Name) {
        auto* Prop = FindFProperty<FBoolProperty>(Actor->GetClass(), Name); check(Prop);
        return Prop->GetPropertyValue_InContainer(Actor);
    };
    auto Control = [Actor](bool Toggle, bool Set, float Value) {
        auto* Fn=Actor->FindFunctionChecked(TEXT("ControlAssembly")); FStructOnScope Args(Fn);
        for(const TCHAR* Name : {TEXT("Progress"),TEXT("FullTravelDuration")})
            if(auto* Input=FindFProperty<FFloatProperty>(Fn,Name))
                Input->SetPropertyValue_InContainer(Args.GetStructMemory(),FindFProperty<FFloatProperty>(Actor->GetClass(),Name)->GetPropertyValue_InContainer(Actor));
        FindFProperty<FBoolProperty>(Fn,TEXT("Toggle"))->SetPropertyValue_InContainer(Args.GetStructMemory(),Toggle);
        if(auto* Flag=FindFProperty<FBoolProperty>(Fn,TEXT("SetProgress")))
        {
            Flag->SetPropertyValue_InContainer(Args.GetStructMemory(),Set);
            FindFProperty<FFloatProperty>(Fn,TEXT("NewProgress"))->SetPropertyValue_InContainer(Args.GetStructMemory(),Value);
        }
        else if(Set)
        {
            auto* User=Actor->FindFunctionChecked(TEXT("UserVariable"));FStructOnScope UserArgs(User);
            FindFProperty<FFloatProperty>(User,TEXT("Progress"))->SetPropertyValue_InContainer(UserArgs.GetStructMemory(),Value);
            FindFProperty<FFloatProperty>(User,TEXT("FullTravelDuration"))->SetPropertyValue_InContainer(UserArgs.GetStructMemory(),FindFProperty<FFloatProperty>(Actor->GetClass(),TEXT("FullTravelDuration"))->GetPropertyValue_InContainer(Actor));
            Actor->ProcessEvent(User,UserArgs.GetStructMemory());
        }
        Actor->ProcessEvent(Fn,Args.GetStructMemory());
    };
    auto SetProgress = [Actor, Mesh, NoUMG, Control](float Value) {
        if(NoUMG)Control(false,true,Value);else Actor->ProcessEvent(Actor->FindFunctionChecked(TEXT("SetProgress")), &Value);
        Mesh->TickAnimation(0.f, false);
        Mesh->RefreshBoneTransforms();
    };
    auto Tick = [Actor](float Delta) {
        Actor->ProcessEvent(Actor->FindFunctionChecked(TEXT("ReceiveTick")), &Delta);
    };
    auto Toggle = [Actor, NoUMG, Control]() { if(NoUMG)Control(true,false,0.f);else Actor->ProcessEvent(Actor->FindFunctionChecked(TEXT("ToggleAssembly")), nullptr); };
    TArray<TSharedPtr<FJsonValue>> Tests;
    int32 Failures = 0;
    auto Check = [&Tests, &Failures](const FString& Name, bool Passed) {
        auto Test = MakeShared<FJsonObject>(); Test->SetStringField(TEXT("name"), Name); Test->SetBoolField(TEXT("passed"), Passed);
        Tests.Add(MakeShared<FJsonValueObject>(Test));
        if (!Passed) { ++Failures; UE_LOG(LogTemp, Error, TEXT("FAILED: %s"), *Name); }
    };
    Check(TEXT("Pure Blueprint actor parent"), BP->ParentClass == AActor::StaticClass());
    if(Root.StartsWith(TEXT("/JC_CustomAssets/")))
    {
        auto& Registry=FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry")).Get();Registry.SearchAllAssets(true);
        TArray<FName> Queue;Queue.Add(BP->GetOutermost()->GetFName());TSet<FName> Seen;
        bool NoOriginalPaths=true;int32 PluginPackages=0;
        for(int32 I=0;I<Queue.Num();++I)
        {
            const FName Package=Queue[I];if(Seen.Contains(Package))continue;Seen.Add(Package);
            if(Package.ToString().StartsWith(TEXT("/Game/"))){NoOriginalPaths=false;UE_LOG(LogTemp,Error,TEXT("Original project dependency remains: %s"),*Package.ToString());}
            if(!Package.ToString().StartsWith(TEXT("/JC_CustomAssets/")))continue;
            ++PluginPackages;TArray<FName> Dependencies;Registry.GetDependencies(Package,Dependencies);Queue.Append(Dependencies);
        }
        Check(TEXT("Plugin dependency closure contains no original Game paths"),NoOriginalPaths);
        Check(TEXT("Plugin dependency closure is nonempty"),PluginPackages>0);
    }
    if(NoUMG)
    {
        Check(TEXT("Actor receives Player 0 keyboard input"),Actor->AutoReceiveInput==EAutoReceiveInput::Player0);
        Check(TEXT("Exactly ControlAssembly and UserVariable"),BP->FunctionGraphs.Num()==2 && Actor->FindFunction(TEXT("ControlAssembly")) && Actor->FindFunction(TEXT("UserVariable")));
        auto* ControlFn=Actor->FindFunctionChecked(TEXT("ControlAssembly"));
        Check(TEXT("Control has only DeltaSeconds and Toggle inputs"),ControlFn->NumParms==2 && FindFProperty<FFloatProperty>(ControlFn,TEXT("DeltaSeconds")) && FindFProperty<FBoolProperty>(ControlFn,TEXT("Toggle")));
        auto* UserFn=Actor->FindFunctionChecked(TEXT("UserVariable"));
        Check(TEXT("UserVariable exposes both float inputs"),UserFn->NumParms==2 && FindFProperty<FFloatProperty>(UserFn,TEXT("Progress")) && FindFProperty<FFloatProperty>(UserFn,TEXT("FullTravelDuration")));
        bool NativeOnly=true;
        for(auto* Graph:BP->FunctionGraphs)for(auto* Node:Graph->Nodes)if(auto* Call=Cast<UK2Node_CallFunction>(Node))NativeOnly &= Call->GetTargetFunction() && Call->GetTargetFunction()->HasAnyFunctionFlags(FUNC_Native);
        Check(TEXT("No custom function called inside ControlAssembly"),NativeOnly);
        Check(TEXT("No macros or timelines"),BP->MacroGraphs.Num()==0 && BP->Timelines.Num()==0);
        bool EventOnly=true; int32 ControlCalls=0;
        for(auto* Graph:BP->UbergraphPages)for(auto* Node:Graph->Nodes)
        {
            if(auto* Call=Cast<UK2Node_CallFunction>(Node)){EventOnly &= Call->FunctionReference.GetMemberName()==TEXT("ControlAssembly");++ControlCalls;}
            else EventOnly &= Node->IsA<UK2Node_Event>() || Node->IsA<UK2Node_InputKey>() || Node->IsA<UK2Node_VariableGet>();
        }
        Check(TEXT("Event Graph only routes BeginPlay Tick N into ControlAssembly"),EventOnly && ControlCalls==3);
        auto* ProgressProp=FindFProperty<FFloatProperty>(Actor->GetClass(),TEXT("Progress"));
        Check(TEXT("Progress editable on runtime instance"),ProgressProp->HasAnyPropertyFlags(CPF_Edit) && !ProgressProp->HasAnyPropertyFlags(CPF_DisableEditOnInstance));
        Check(TEXT("Progress slider range 0 to 1"),ProgressProp->GetMetaData(TEXT("UIMin"))==TEXT("0") && ProgressProp->GetMetaData(TEXT("UIMax"))==TEXT("1"));
        Toggle(); Tick(.3f);
        ProgressProp->SetPropertyValue_InContainer(Actor,.283f); Tick(.1f);
        Check(TEXT("Details property edit takes over on next tick"),FMath::IsNearlyEqual(ReadFloat(TEXT("Progress")),.283f,1.e-5f) && !ReadBool(TEXT("bAutoPlaying")));
    }
    Check(TEXT("SingleNode mode"), Mesh->GetSingleNodeInstance() != nullptr);
    Check(TEXT("Animation does not self-play"), !Mesh->IsPlaying());
    SetProgress(-1.f); Check(TEXT("Clamp below zero"), ReadFloat(TEXT("Progress")) == 0.f);
    SetProgress(2.f); Check(TEXT("Clamp above one"), ReadFloat(TEXT("Progress")) == 1.f);
    Toggle(); Tick(.6f); Check(TEXT("Button assembles from full state"), FMath::IsNearlyEqual(ReadFloat(TEXT("Progress")), .8f, 1.e-5f));
    float BeforeReverse = ReadFloat(TEXT("Progress")); Toggle();
    Check(TEXT("Reverse has no position jump"), ReadFloat(TEXT("Progress")) == BeforeReverse);
    Tick(.3f); Check(TEXT("Reverse uses constant speed"), FMath::IsNearlyEqual(ReadFloat(TEXT("Progress")), .9f));
    SetProgress(.37f); Check(TEXT("Slider takes over"), !ReadBool(TEXT("bAutoPlaying")));
    Tick(.6f); Check(TEXT("Manual state holds"), FMath::IsNearlyEqual(ReadFloat(TEXT("Progress")), .37f));
    Toggle(); Tick(.3f); Check(TEXT("Partial manual state resumes outward"), FMath::IsNearlyEqual(ReadFloat(TEXT("Progress")), .47f, 1.e-5f));
    Tick(20.f); Check(TEXT("Auto reaches exact end and stops"), ReadFloat(TEXT("Progress")) == 1.f && !ReadBool(TEXT("bAutoPlaying")));
    Toggle(); Tick(20.f); Check(TEXT("Auto reaches exact start and stops"), ReadFloat(TEXT("Progress")) == 0.f && !ReadBool(TEXT("bAutoPlaying")));
    SetProgress(.619f); auto Reference = Mesh->GetComponentSpaceTransforms();
    for (float P : {1.f, .1f, .947f, 0.f, .283f, .619f}) SetProgress(P);
    bool Same = Reference.Num() == Mesh->GetComponentSpaceTransforms().Num();
    for (int32 I=0; Same && I<Reference.Num(); ++I) Same &= Reference[I].Equals(Mesh->GetComponentSpaceTransforms()[I], 1.e-5f);
    Check(TEXT("Random seeks are deterministic"), Same);

    FString Text; TSharedPtr<FJsonObject> Source;
    if (!FFileHelper::LoadFileToString(Text, *SamplesPath) || !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text), Source)) return 4;
    const bool YUp = Source->GetObjectField(TEXT("coordinateConvention"))->GetStringField(TEXT("upAxis")) == TEXT("y");
    const FMatrix Basis = YUp ? FMatrix(FPlane(1,0,0,0), FPlane(0,0,1,0), FPlane(0,1,0,0), FPlane(0,0,0,1))
                                : FMatrix(FPlane(1,0,0,0), FPlane(0,-1,0,0), FPlane(0,0,1,0), FPlane(0,0,0,1));
    double MaxPositionError = 0, MaxRotationError = 0;
    SetProgress(0.f);
    const auto RestUE = Mesh->GetComponentSpaceTransforms();
    const auto RestMaya = Source->GetArrayField(TEXT("samples"))[0]->AsObject()->GetObjectField(TEXT("bones"));
    TArray<TSharedPtr<FJsonValue>> PoseReports;
    for (const auto& SampleValue : Source->GetArrayField(TEXT("samples")))
    {
        auto Sample = SampleValue->AsObject(); float P = Sample->GetNumberField(TEXT("progress")); SetProgress(P);
        auto Bones = Sample->GetObjectField(TEXT("bones"));
        double PositionError = 0, RotationError = 0;
        for (const auto& Pair : Bones->Values)
        {
            const int32 Index = Mesh->GetBoneIndex(*Pair.Key);
            if (Index == INDEX_NONE) { Check(TEXT("Missing bone ") + Pair.Key, false); continue; }
            const auto Values = Pair.Value->AsObject()->GetArrayField(TEXT("matrixRowMajor"));
            FMatrix M; for (int32 R=0; R<4; ++R) for (int32 C=0; C<4; ++C) M.M[R][C] = Values[R*4+C]->AsNumber();
            const FTransform Expected(Basis.Inverse() * M * Basis);
            const FTransform Actual = Mesh->GetComponentSpaceTransforms()[Index];
            PositionError = FMath::Max(PositionError, double(FVector::Distance(Actual.GetTranslation(), Expected.GetTranslation())));
            // FBX changes bone-local axes. Compare model-space motion relative to rest,
            // not the arbitrary orientation of an individual joint's local frame.
            const auto RestValues = RestMaya->GetObjectField(Pair.Key)->GetArrayField(TEXT("matrixRowMajor"));
            FMatrix Rest; for (int32 R=0; R<4; ++R) for (int32 C=0; C<4; ++C) Rest.M[R][C] = RestValues[R*4+C]->AsNumber();
            const FTransform ExpectedDelta(Basis.Inverse() * Rest.Inverse() * M * Basis);
            const FTransform ActualDelta(RestUE[Index].ToMatrixWithScale().Inverse() * Actual.ToMatrixWithScale());
            RotationError = FMath::Max(RotationError, double(FMath::RadiansToDegrees(ActualDelta.GetRotation().AngularDistance(ExpectedDelta.GetRotation()))));
        }
        MaxPositionError = FMath::Max(MaxPositionError, PositionError); MaxRotationError = FMath::Max(MaxRotationError, RotationError);
        auto Row = MakeShared<FJsonObject>(); Row->SetNumberField(TEXT("progress"), P); Row->SetNumberField(TEXT("positionErrorCm"), PositionError); Row->SetNumberField(TEXT("rotationErrorDegrees"), RotationError);
        PoseReports.Add(MakeShared<FJsonValueObject>(Row));
    }
    Check(TEXT("Maya to Unreal sampled bone positions <= 0.01 cm"), MaxPositionError <= .01);
    Check(TEXT("Maya to Unreal sampled bone rotations <= 0.1 degrees"), MaxRotationError <= .1);
    Check(TEXT("Expected bone count"), Mesh->GetNumBones() == int32(Source->GetNumberField(TEXT("boneCount"))));
    Check(TEXT("Expected duration"), FMath::IsNearlyEqual(Mesh->GetSingleNodeInstance()->GetLength(), float(Source->GetNumberField(TEXT("durationSeconds"))), 1.e-4f));
    const double Start = FPlatformTime::Seconds();
    for (int32 I=0; I<200; ++I) SetProgress(float((I*37)%101)/100.f);
    const double MeanPoseMs = (FPlatformTime::Seconds()-Start)*1000.0/200.0;
    auto Report = MakeShared<FJsonObject>(); Report->SetBoolField(TEXT("passed"), Failures==0); Report->SetNumberField(TEXT("failures"), Failures);
    Report->SetArrayField(TEXT("tests"), Tests); Report->SetArrayField(TEXT("poseSamples"), PoseReports);
    Report->SetNumberField(TEXT("maxPositionErrorCm"), MaxPositionError); Report->SetNumberField(TEXT("maxRotationErrorDegrees"), MaxRotationError);
    Report->SetNumberField(TEXT("meanPoseUpdateMs"), MeanPoseMs); Report->SetStringField(TEXT("performanceScope"), TEXT("CPU pose update in commandlet; not rendered FPS"));
    FString Output; FJsonSerializer::Serialize(Report, TJsonWriterFactory<>::Create(&Output));
    if (!ReportPath.IsEmpty()) FFileHelper::SaveStringToFile(Output, *ReportPath);
    UE_LOG(LogTemp, Display, TEXT("OPENMODEL_VALIDATE failures=%d positionError=%g rotationError=%g meanPoseMs=%g"), Failures, MaxPositionError, MaxRotationError, MeanPoseMs);
    return Failures ? 1 : 0;
}
