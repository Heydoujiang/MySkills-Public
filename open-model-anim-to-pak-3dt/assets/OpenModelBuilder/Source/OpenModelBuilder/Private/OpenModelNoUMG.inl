// Included in the builder translation unit to reuse editor graph-authoring helpers.
#include "OpenModelUserVariable.inl"
#include "OpenModelPakBranch.inl"
#include "OpenModelCollision.inl"
#include "GameFramework/PlayerStart.h"
namespace OpenModelBuild
{
static int32 RemoveFixedCamera(UWorld* World)
{
    TArray<ACameraActor*> Cameras;
    for(TActorIterator<ACameraActor> It(World);It;++It)Cameras.Add(*It);
    bool HasStart=false;for(TActorIterator<APlayerStart> It(World);It;++It)HasStart=true;
    if(!HasStart && Cameras.Num())
    {
        auto* Start=World->SpawnActor<APlayerStart>(APlayerStart::StaticClass(),Cameras[0]->GetActorTransform());
        check(Start);Start->SetActorLabel(TEXT("Free View Start"));
    }
    for(auto* Camera:Cameras)World->DestroyActor(Camera);
    return Cameras.Num();
}
static UBlueprint* MakeNoUMG(bool Rebuild)
{
    const FString Folder=TEXT("/Game/OpenModel/NoUMG");
    const FString Name=TEXT("BP_ExplodedAssembly_NoUMG");
    auto* Source=LoadObject<UBlueprint>(nullptr,TEXT("/Game/OpenModel/BP_ExplodedAssembly.BP_ExplodedAssembly"));
    check(Source && Source->ParentClass==AActor::StaticClass());
    const bool Exists=FPackageName::DoesPackageExist(Folder+TEXT("/")+Name);
    checkf(!Exists || Rebuild,TEXT("NoUMG asset already exists; pass -Rebuild only to intentionally replace its control graphs"));
    auto* BP=Exists?LoadObject<UBlueprint>(nullptr,*(Folder+TEXT("/")+Name+TEXT(".")+Name)):
        CastChecked<UBlueprint>(FAssetToolsModule::GetModule().Get().DuplicateAsset(Name,Folder,Source));
    check(BP);
    // Work on the duplicate only, retaining the source's component setup and asset references.
    TArray<UEdGraph*> Graphs=BP->FunctionGraphs;
    Graphs.Append(BP->UbergraphPages); Graphs.Append(BP->MacroGraphs);
    FBlueprintEditorUtils::RemoveGraphs(BP,Graphs);
    TArray<FName> RemoveVars;
    for (const auto& V:BP->NewVariables)
        if (V.VarName!=TEXT("Progress") && V.VarName!=TEXT("FullTravelDuration") && V.VarName!=TEXT("ExplosionAnimation")) RemoveVars.Add(V.VarName);
    FBlueprintEditorUtils::BulkRemoveMemberVariables(BP,RemoveVars);
    AddVar(BP,TEXT("TargetProgress"),FloatT(),TEXT("0"));
    AddVar(BP,TEXT("LastAppliedProgress"),FloatT(),TEXT("0"));
    AddVar(BP,TEXT("bAutoPlaying"),BoolT(),TEXT("false"));
    AddVar(BP,TEXT("bInitialized"),BoolT(),TEXT("false"));
    for (FName V:{FName(TEXT("TargetProgress")),FName(TEXT("LastAppliedProgress")),FName(TEXT("bAutoPlaying")),FName(TEXT("bInitialized"))})
    {
        FBlueprintEditorUtils::SetBlueprintOnlyEditableFlag(BP,V,true);
        FBlueprintEditorUtils::SetBlueprintVariableCategory(BP,V,nullptr,FText::FromString(TEXT("Assembly Internal")));
    }
    FBlueprintEditorUtils::SetBlueprintOnlyEditableFlag(BP,TEXT("Progress"),false);
    for (const TCHAR* Key:{TEXT("ClampMin"),TEXT("UIMin")}) FBlueprintEditorUtils::SetBlueprintVariableMetaData(BP,TEXT("Progress"),nullptr,Key,TEXT("0"));
    for (const TCHAR* Key:{TEXT("ClampMax"),TEXT("UIMax")}) FBlueprintEditorUtils::SetBlueprintVariableMetaData(BP,TEXT("Progress"),nullptr,Key,TEXT("1"));
    FBlueprintEditorUtils::SetBlueprintVariableMetaData(BP,TEXT("Progress"),nullptr,TEXT("ToolTip"),TEXT("0=assembled, 1=exploded. During PIE edit this runtime instance (F8); editing interrupts automatic travel."));
    FBlueprintEditorUtils::SetBlueprintOnlyEditableFlag(BP,TEXT("FullTravelDuration"),false);
    FBlueprintEditorUtils::SetBlueprintVariableMetaData(BP,TEXT("FullTravelDuration"),nullptr,TEXT("ClampMin"),TEXT("0.01"));

    auto F=Func(BP,TEXT("ControlAssembly"));
    F.Entry->CreateUserDefinedPin(TEXT("DeltaSeconds"),FloatT(),EGPD_Output);
    F.Entry->CreateUserDefinedPin(TEXT("Toggle"),BoolT(),EGPD_Output);


    Compile(BP);
    auto* G=F.Graph;
    auto Input=[&](FName N){return P(F.Entry,N);};
    auto MeshCall=[&](FName N){auto* C=Call(G,USkeletalMeshComponent::StaticClass(),N); Wire(Val(G,TEXT("AssemblyMesh")),P(C,UEdGraphSchema_K2::PN_Self)); return C;};
    auto Note=[](UEdGraphNode* N,const TCHAR* Text){N->NodeComment=Text; N->bCommentBubbleVisible=true;};
    auto* Init=Branch(G,Val(G,TEXT("bInitialized"))); Exec(F.Entry,Init); Note(Init,TEXT("1. Initialize once; all runtime logic stays in this function"));
    auto* Mode=MeshCall(TEXT("SetAnimationMode")); Def(Mode,TEXT("InAnimationMode"),TEXT("AnimationSingleNode")); Exec(Init,Mode,UEdGraphSchema_K2::PN_Else);
    auto* Animation=MeshCall(TEXT("SetAnimation")); Wire(Val(G,TEXT("ExplosionAnimation")),P(Animation,TEXT("NewAnimToPlay"))); Exec(Mode,Animation);
    auto* Stop=MeshCall(TEXT("Stop")); Exec(Animation,Stop);
    auto* Initialized=Set(G,TEXT("bInitialized")); Def(Initialized,TEXT("bInitialized"),TEXT("true")); Exec(Stop,Initialized);



    auto* Changed=Math(G,TEXT("NotEqual_FloatFloat")); Wire(Val(G,TEXT("Progress")),P(Changed,TEXT("A"))); Wire(Val(G,TEXT("LastAppliedProgress")),P(Changed,TEXT("B")));
    auto* Edit=Branch(G,Out(Changed)); Exec(Init,Edit); Exec(Initialized,Edit);
    auto* Manual=Set(G,TEXT("bAutoPlaying")); Def(Manual,TEXT("bAutoPlaying"),TEXT("false")); Exec(Edit,Manual);
    auto* Clamp=Math(G,TEXT("FClamp")); Wire(Val(G,TEXT("Progress")),P(Clamp,TEXT("Value"))); Def(Clamp,TEXT("Min"),TEXT("0")); Def(Clamp,TEXT("Max"),TEXT("1"));
    auto* Clamped=Set(G,TEXT("Progress")); Wire(Out(Clamp),P(Clamped,TEXT("Progress"))); Exec(Manual,Clamped); Exec(Edit,Clamped,UEdGraphSchema_K2::PN_Else);

    auto* Toggle=Branch(G,Input(TEXT("Toggle"))); Exec(Clamped,Toggle); Note(Toggle,TEXT("3. N / Toggle: reverse moving target; manual middle resumes outward"));
    auto* Moving=Branch(G,Val(G,TEXT("bAutoPlaying"))); Exec(Toggle,Moving);
    auto* Reverse=Math(G,TEXT("Subtract_FloatFloat")); Def(Reverse,TEXT("A"),TEXT("1")); Wire(Val(G,TEXT("TargetProgress")),P(Reverse,TEXT("B")));
    auto* Reversed=Set(G,TEXT("TargetProgress")); Wire(Out(Reverse),P(Reversed,TEXT("TargetProgress"))); Exec(Moving,Reversed);
    auto* Full=Math(G,TEXT("GreaterEqual_FloatFloat")); Wire(Val(G,TEXT("Progress")),P(Full,TEXT("A"))); Def(Full,TEXT("B"),TEXT("1"));
    auto* Target=Math(G,TEXT("SelectFloat")); Def(Target,TEXT("A"),TEXT("0")); Def(Target,TEXT("B"),TEXT("1")); Wire(Out(Full),P(Target,TEXT("bPickA")));
    auto* TargetSet=Set(G,TEXT("TargetProgress")); Wire(Out(Target),P(TargetSet,TEXT("TargetProgress"))); Exec(Moving,TargetSet,UEdGraphSchema_K2::PN_Else);
    auto* Play=Set(G,TEXT("bAutoPlaying")); Def(Play,TEXT("bAutoPlaying"),TEXT("true")); Exec(Reversed,Play); Exec(TargetSet,Play);

    auto* Advance=Branch(G,Val(G,TEXT("bAutoPlaying"))); Exec(Play,Advance); Exec(Toggle,Advance,UEdGraphSchema_K2::PN_Else); Note(Advance,TEXT("4. Constant progress speed; no Timeline"));
    auto* Duration=Math(G,TEXT("FMax")); Wire(Val(G,TEXT("FullTravelDuration")),P(Duration,TEXT("A"))); Def(Duration,TEXT("B"),TEXT("0.01"));
    auto* Speed=Math(G,TEXT("Divide_FloatFloat")); Def(Speed,TEXT("A"),TEXT("1")); Wire(Out(Duration),P(Speed,TEXT("B")));
    auto* Delta=Math(G,TEXT("FMax")); Wire(Input(TEXT("DeltaSeconds")),P(Delta,TEXT("A"))); Def(Delta,TEXT("B"),TEXT("0"));
    auto* Interp=Math(G,TEXT("FInterpTo_Constant")); Wire(Val(G,TEXT("Progress")),P(Interp,TEXT("Current"))); Wire(Val(G,TEXT("TargetProgress")),P(Interp,TEXT("Target"))); Wire(Out(Delta),P(Interp,TEXT("DeltaTime"))); Wire(Out(Speed),P(Interp,TEXT("InterpSpeed")));
    auto* Next=Set(G,TEXT("Progress")); Wire(Out(Interp),P(Next,TEXT("Progress"))); Exec(Advance,Next);

    auto* Len=Call(G,UAnimSequenceBase::StaticClass(),TEXT("GetPlayLength")); Wire(Val(G,TEXT("ExplosionAnimation")),P(Len,UEdGraphSchema_K2::PN_Self)); Exec(Next,Len); Exec(Advance,Len,UEdGraphSchema_K2::PN_Else); Note(Len,TEXT("5. Absolute animation sampling and remembered applied progress"));
    auto* Time=Math(G,TEXT("Multiply_FloatFloat")); Wire(Val(G,TEXT("Progress")),P(Time,TEXT("A"))); Wire(Out(Len),P(Time,TEXT("B")));
    auto* Position=MeshCall(TEXT("SetPosition")); Wire(Out(Time),P(Position,TEXT("InPos"))); Def(Position,TEXT("bFireNotifies"),TEXT("false")); Exec(Len,Position);
    auto* Last=Set(G,TEXT("LastAppliedProgress")); Wire(Val(G,TEXT("Progress")),P(Last,TEXT("LastAppliedProgress"))); Exec(Position,Last);
    auto* Different=Math(G,TEXT("NotEqual_FloatFloat")); Wire(Val(G,TEXT("Progress")),P(Different,TEXT("A"))); Wire(Val(G,TEXT("TargetProgress")),P(Different,TEXT("B")));
    auto* KeepPlaying=Math(G,TEXT("BooleanAND")); Wire(Val(G,TEXT("bAutoPlaying")),P(KeepPlaying,TEXT("A"))); Wire(Out(Different),P(KeepPlaying,TEXT("B")));
    auto* Active=Set(G,TEXT("bAutoPlaying")); Wire(Out(KeepPlaying),P(Active,TEXT("bAutoPlaying"))); Exec(Last,Active); Exec(Active,F.Result);
    F.Entry->NodePosX=-350; F.Entry->NodePosY=0;
    F.Result->NodePosX=Active->NodePosX+350; F.Result->NodePosY=Active->NodePosY;

    auto User=Func(BP,TEXT("UserVariable"));
    User.Entry->CreateUserDefinedPin(TEXT("Progress"),FloatT(),EGPD_Output);
    User.Entry->CreateUserDefinedPin(TEXT("FullTravelDuration"),FloatT(),EGPD_Output);
    auto* UserProgress=Set(User.Graph,TEXT("Progress"));auto* UserDuration=Set(User.Graph,TEXT("FullTravelDuration"));
    Wire(P(User.Entry,TEXT("Progress")),P(UserProgress,TEXT("Progress")));
    Wire(P(User.Entry,TEXT("FullTravelDuration")),P(UserDuration,TEXT("FullTravelDuration")));
    Exec(User.Entry,UserProgress);Exec(UserProgress,UserDuration);Exec(UserDuration,User.Result);
    User.Entry->NodePosX=0;User.Entry->NodePosY=0;UserProgress->NodePosX=350;UserProgress->NodePosY=0;
    UserDuration->NodePosX=700;UserDuration->NodePosY=0;User.Result->NodePosX=1050;User.Result->NodePosY=0;
    auto* Events=EventGraph(BP); LayoutIndex=0;
    auto* Begin=Event(Events,AActor::StaticClass(),TEXT("ReceiveBeginPlay")); auto* BeginCall=Call(Events,BP->GeneratedClass,TEXT("ControlAssembly")); Exec(Begin,BeginCall);
    auto* Tick=Event(Events,AActor::StaticClass(),TEXT("ReceiveTick")); auto* TickCall=Call(Events,BP->GeneratedClass,TEXT("ControlAssembly")); Wire(P(Tick,TEXT("DeltaSeconds")),P(TickCall,TEXT("DeltaSeconds"))); Exec(Tick,TickCall);
    auto* Key=NewNode<UK2Node_InputKey>(Events); Key->InputKey=EKeys::N; Key->bConsumeInput=true; Key->AllocateDefaultPins();
    auto* KeyCall=Call(Events,BP->GeneratedClass,TEXT("ControlAssembly")); Def(KeyCall,TEXT("Toggle"),TEXT("true")); Wire(Key->GetPressedPin(),P(KeyCall,UEdGraphSchema_K2::PN_Execute));
    Begin->NodePosX=0; Begin->NodePosY=0; BeginCall->NodePosX=350; BeginCall->NodePosY=0;
    Tick->NodePosX=0; Tick->NodePosY=300; TickCall->NodePosX=350; TickCall->NodePosY=300;
    Key->NodePosX=0; Key->NodePosY=600; KeyCall->NodePosX=350; KeyCall->NodePosY=600;
    Compile(BP);
    auto* CDO=CastChecked<AActor>(BP->GeneratedClass->GetDefaultObject());
    CDO->AutoReceiveInput=EAutoReceiveInput::Player0;
    CDO->PrimaryActorTick.bCanEverTick=true; CDO->PrimaryActorTick.bStartWithTickEnabled=true;
    FindFProperty<FFloatProperty>(CDO->GetClass(),TEXT("Progress"))->SetPropertyValue_InContainer(CDO,0.f);
    Save(BP); return BP;
}
}

UOpenModelNoUMGCommandlet::UOpenModelNoUMGCommandlet(){IsClient=false; IsEditor=true; IsServer=false; LogToConsole=true;}
int32 UOpenModelNoUMGCommandlet::Main(const FString& Params)
{
    if(FParse::Param(*Params,TEXT("EnsureCollision")))return OpenModelBuild::EnsureMotionCollision(Params);
    if(FParse::Param(*Params,TEXT("PreparePakBranch")))return OpenModelBuild::PreparePakBranch(Params);
    if(FParse::Param(*Params,TEXT("SplitUserVariable")))return OpenModelBuild::SplitUserVariable();
    if(FParse::Param(*Params,TEXT("PreparePackage")))return OpenModelBuild::PreparePackage(Params);
    if(FParse::Param(*Params,TEXT("FreeCamera")))
    {
        check(FEditorFileUtils::LoadMap(TEXT("/Game/OpenModel/NoUMG/Demo_ExplodedAssembly_NoUMG"),false,true));
        auto* World=GEditor->GetEditorWorldContext().World();
        int32 Removed=OpenModelBuild::RemoveFixedCamera(World);
        FString OutputMap=TEXT("/Game/OpenModel/NoUMG/Demo_ExplodedAssembly_NoUMG");
        FParse::Value(*Params,TEXT("OutputMap="),OutputMap);
        check(FPackageName::IsValidLongPackageName(OutputMap));
        const FString Map=FPackageName::LongPackageNameToFilename(OutputMap,FPackageName::GetMapPackageExtension());
        check(FEditorFileUtils::SaveMap(World,Map));
        int32 Remaining=0;for(TActorIterator<ACameraActor> It(World);It;++It)++Remaining;
        check(Remaining==0);
        UE_LOG(LogTemp,Display,TEXT("OPENMODEL_NOUMG_FREE_CAMERA removed=%d remaining=%d"),Removed,Remaining);
        return 0;
    }
    auto* BP=OpenModelBuild::MakeNoUMG(FParse::Param(*Params,TEXT("Rebuild")));
    check(FEditorFileUtils::LoadMap(TEXT("/Game/OpenModel/Demo_ExplodedAssembly"),false,true));
    auto* World=GEditor->GetEditorWorldContext().World();
    TArray<AActor*> Remove;
    FTransform Transform=FTransform::Identity;
    for(TActorIterator<AActor> It(World);It;++It)
    {
        const FString Class=It->GetClass()->GetName();
        if(Class==TEXT("BP_ExplodedAssembly_C")){Transform=It->GetActorTransform();Remove.Add(*It);}
        if(Class==TEXT("BP_AssemblyUIHost_C"))Remove.Add(*It);
    }
    for(auto* Actor:Remove)World->DestroyActor(Actor);
    auto* Actor=World->SpawnActor<AActor>(BP->GeneratedClass,Transform); Actor->SetActorLabel(TEXT("Exploded Assembly - N Key and Progress"));
    OpenModelBuild::RemoveFixedCamera(World);
    const FString Map=FPackageName::LongPackageNameToFilename(TEXT("/Game/OpenModel/NoUMG/Demo_ExplodedAssembly_NoUMG"),FPackageName::GetMapPackageExtension());
    check(FEditorFileUtils::SaveMap(World,Map));
    UE_LOG(LogTemp,Display,TEXT("OPENMODEL_NOUMG_OK %s Functions=ControlAssembly,UserVariable Input=N"),*BP->GetPathName());
    return 0;
}
