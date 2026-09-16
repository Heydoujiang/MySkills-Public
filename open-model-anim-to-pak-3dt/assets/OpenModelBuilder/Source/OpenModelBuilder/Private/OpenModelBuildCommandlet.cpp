#include "OpenModelBuildCommandlet.h"
#include "K2Node_InputKey.h"
#include "EngineUtils.h"
#include "AssetToolsModule.h"
#include "AssetImportTask.h"
#include "AssetRegistryModule.h"
#include "Factories/FbxFactory.h"
#include "Factories/FbxImportUI.h"
#include "Factories/FbxSkeletalMeshImportData.h"
#include "Factories/FbxAnimSequenceImportData.h"
#include "Kismet2/KismetEditorUtilities.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "KismetCompiler.h"
#include "EdGraphSchema_K2.h"
#include "K2Node_CallFunction.h"
#include "K2Node_VariableGet.h"
#include "K2Node_VariableSet.h"
#include "K2Node_Event.h"
#include "K2Node_FunctionEntry.h"
#include "K2Node_FunctionResult.h"
#include "K2Node_IfThenElse.h"
#include "K2Node_DynamicCast.h"
#include "K2Node_ComponentBoundEvent.h"
#include "Nodes/K2Node_CreateWidget.h"
#include "K2Node_Self.h"
#include "Engine/Blueprint.h"
#include "Engine/SimpleConstructionScript.h"
#include "Engine/SCS_Node.h"
#include "Animation/AnimSequence.h"
#include "Animation/AnimBoneCompressionSettings.h"
#include "Animation/AnimCompress_BitwiseCompressOnly.h"
#include "Animation/AnimSingleNodeInstance.h"
#include "Components/SkeletalMeshComponent.h"
#include "Components/SceneComponent.h"
#include "Kismet/KismetMathLibrary.h"
#include "Kismet/GameplayStatics.h"
#include "Blueprint/WidgetBlueprintLibrary.h"
#include "Blueprint/UserWidget.h"
#include "Blueprint/WidgetTree.h"
#include "Blueprint/WidgetBlueprintGeneratedClass.h"
#include "WidgetBlueprint.h"
#include "Components/CanvasPanel.h"
#include "Components/CanvasPanelSlot.h"
#include "Components/VerticalBox.h"
#include "Components/VerticalBoxSlot.h"
#include "Components/TextBlock.h"
#include "Components/Slider.h"
#include "Components/Button.h"
#include "Components/Border.h"
#include "Components/SizeBox.h"
#include "GameFramework/PlayerController.h"
#include "GameFramework/WorldSettings.h"
#include "Camera/CameraActor.h"
#include "Camera/CameraComponent.h"
#include "Engine/DirectionalLight.h"
#include "Components/DirectionalLightComponent.h"
#include "Engine/SkyLight.h"
#include "Components/SkyLightComponent.h"
#include "Engine/StaticMeshActor.h"
#include "Components/StaticMeshComponent.h"
#include "Editor.h"
#include "FileHelpers.h"
#include "Misc/PackageName.h"
#include "Misc/FileHelper.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"

namespace OpenModelBuild
{
static const UEdGraphSchema_K2* Schema = GetDefault<UEdGraphSchema_K2>();
static int32 LayoutIndex = 0;
static FString ContentRoot = TEXT("/Game/OpenModel");
static FEdGraphPinType Type(FName Category, UObject* Object=nullptr) { FEdGraphPinType T; T.PinCategory=Category; T.PinSubCategoryObject=Object; return T; }
static FEdGraphPinType FloatT() { return Type(UEdGraphSchema_K2::PC_Float); }
static FEdGraphPinType BoolT() { return Type(UEdGraphSchema_K2::PC_Boolean); }
static FEdGraphPinType ObjectT(UClass* C) { return Type(UEdGraphSchema_K2::PC_Object,C); }
template<class T> static T* NewNode(UEdGraph* G) { T* N=NewObject<T>(G); G->AddNode(N,true,false); N->CreateNewGuid(); N->NodePosX=(LayoutIndex%5)*310; N->NodePosY=(LayoutIndex/5)*230; ++LayoutIndex; return N; }
static UEdGraphPin* P(UEdGraphNode* N, FName Name) { UEdGraphPin* R=N->FindPin(Name); if(!R) { FString Pins; for(auto* Pin:N->Pins) Pins+=Pin->PinName.ToString()+TEXT(","); UE_LOG(LogTemp,Fatal,TEXT("Missing pin %s on %s; available %s"),*Name.ToString(),*N->GetClass()->GetName(),*Pins); } return R; }
static UEdGraphPin* Out(UEdGraphNode* N) { return P(N,UEdGraphSchema_K2::PN_ReturnValue); }
static void Wire(UEdGraphPin* A,UEdGraphPin* B) { if(!Schema->TryCreateConnection(A,B)) UE_LOG(LogTemp,Fatal,TEXT("Connection rejected %s.%s -> %s.%s"),*A->GetOwningNode()->GetName(),*A->PinName.ToString(),*B->GetOwningNode()->GetName(),*B->PinName.ToString()); }
static void Exec(UEdGraphNode* A,UEdGraphNode* B,FName Output=UEdGraphSchema_K2::PN_Then) { Wire(P(A,Output),P(B,UEdGraphSchema_K2::PN_Execute)); }
static void Def(UEdGraphNode* N,FName Name,const FString& V) { Schema->TrySetDefaultValue(*P(N,Name),V); }
static void Obj(UEdGraphNode* N,FName Name,UObject* O) { Schema->TrySetDefaultObject(*P(N,Name),O); }
static UK2Node_CallFunction* Call(UEdGraph* G,UClass* C,FName Name) { auto* N=NewNode<UK2Node_CallFunction>(G); UFunction* F=C->FindFunctionByName(Name); checkf(F,TEXT("Missing function %s.%s"),*C->GetName(),*Name.ToString()); N->SetFromFunction(F); N->AllocateDefaultPins(); return N; }
static UK2Node_CallFunction* Math(UEdGraph* G,FName Name) { return Call(G,UKismetMathLibrary::StaticClass(),Name); }
static UK2Node_VariableGet* Get(UEdGraph* G,FName Name,UClass* External=nullptr) { auto* N=NewNode<UK2Node_VariableGet>(G); if(External)N->VariableReference.SetExternalMember(Name,External);else N->VariableReference.SetSelfMember(Name); N->AllocateDefaultPins(); return N; }
static UK2Node_VariableSet* Set(UEdGraph* G,FName Name,UClass* External=nullptr) { auto* N=NewNode<UK2Node_VariableSet>(G); if(External)N->VariableReference.SetExternalMember(Name,External);else N->VariableReference.SetSelfMember(Name); N->AllocateDefaultPins(); return N; }
static UEdGraphPin* Val(UEdGraph* G,FName Name) { return P(Get(G,Name),Name); }
static UK2Node_IfThenElse* Branch(UEdGraph* G,UEdGraphPin* Condition) { auto* N=NewNode<UK2Node_IfThenElse>(G); N->AllocateDefaultPins(); Wire(Condition,P(N,UEdGraphSchema_K2::PN_Condition)); return N; }
static UK2Node_Event* Event(UEdGraph* G,UClass* Base,FName Name) { auto* N=NewNode<UK2Node_Event>(G); N->EventReference.SetExternalMember(Name,Base); N->bOverrideFunction=true; N->AllocateDefaultPins(); return N; }
static UEdGraph* EventGraph(UBlueprint* BP) { if(BP->UbergraphPages.Num()) return BP->UbergraphPages[0]; auto* G=FBlueprintEditorUtils::CreateNewGraph(BP,TEXT("EventGraph"),UEdGraph::StaticClass(),UEdGraphSchema_K2::StaticClass()); FBlueprintEditorUtils::AddUbergraphPage(BP,G); return G; }
struct Function { UEdGraph* Graph; UK2Node_FunctionEntry* Entry; UK2Node_FunctionResult* Result; };
static Function Func(UBlueprint* BP,FName Name,bool Input=false,bool Output=false) { LayoutIndex=0; auto* G=FBlueprintEditorUtils::CreateNewGraph(BP,Name,UEdGraph::StaticClass(),UEdGraphSchema_K2::StaticClass()); FBlueprintEditorUtils::AddFunctionGraph<UFunction>(BP,G,true,nullptr); UK2Node_FunctionEntry* E=nullptr; for(auto* N:G->Nodes)if(auto* V=Cast<UK2Node_FunctionEntry>(N))E=V; check(E); E->AddExtraFlags(FUNC_Public|FUNC_BlueprintCallable); if(Input)E->CreateUserDefinedPin(TEXT("Value"),FloatT(),EGPD_Output); auto* R=NewNode<UK2Node_FunctionResult>(G); R->FunctionReference=E->FunctionReference; R->AllocateDefaultPins(); if(Output)R->CreateUserDefinedPin(TEXT("ReturnValue"),FloatT(),EGPD_Input); return {G,E,R}; }
static void Compile(UBlueprint* BP) { FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(BP); FCompilerResultsLog Log; FKismetEditorUtilities::CompileBlueprint(BP,EBlueprintCompileOptions::SkipGarbageCollection,&Log); if(Log.NumErrors)UE_LOG(LogTemp,Fatal,TEXT("Blueprint %s compile failed: %d errors"),*BP->GetName(),Log.NumErrors); }
static UBlueprint* NewBP(FString Name,UClass* Parent,bool Widget=false) { FString Path=ContentRoot+TEXT("/")+Name; UPackage* Package=CreatePackage(*Path); checkf(!FindObject<UObject>(Package,*Name),TEXT("Run builder in fresh process; existing loaded asset %s"),*Name); auto* BP=FKismetEditorUtilities::CreateBlueprint(Parent,Package,*Name,BPTYPE_Normal,Widget?UWidgetBlueprint::StaticClass():UBlueprint::StaticClass(),Widget?UWidgetBlueprintGeneratedClass::StaticClass():UBlueprintGeneratedClass::StaticClass(),TEXT("OpenModelBuilder")); FAssetRegistryModule::AssetCreated(BP); return BP; }
static void Save(UObject* Asset) { auto* Package=Asset->GetOutermost(); FString Path=FPackageName::LongPackageNameToFilename(Package->GetName(),FPackageName::GetAssetPackageExtension()); Package->MarkPackageDirty(); check(UPackage::SavePackage(Package,Asset,RF_Public|RF_Standalone,*Path,GError,nullptr,false,true,SAVE_NoError)); }
static void AddVar(UBlueprint* BP,FName Name,FEdGraphPinType T,FString Default=TEXT("")) { check(FBlueprintEditorUtils::AddMemberVariable(BP,Name,T,Default)); FBlueprintEditorUtils::SetBlueprintVariableCategory(BP,Name,nullptr,FText::FromString(TEXT("Assembly"))); }

static UBlueprint* MakeAssembly(USkeletalMesh* Mesh,UAnimSequence* Anim)
{
    auto* BP=NewBP(TEXT("BP_ExplodedAssembly"),AActor::StaticClass());
    AddVar(BP,TEXT("Progress"),FloatT(),TEXT("0")); AddVar(BP,TEXT("TargetProgress"),FloatT(),TEXT("0")); AddVar(BP,TEXT("bAutoPlaying"),BoolT(),TEXT("false")); AddVar(BP,TEXT("FullTravelDuration"),FloatT(),TEXT("3")); AddVar(BP,TEXT("ExplosionAnimation"),ObjectT(UAnimSequence::StaticClass()),Anim->GetPathName());
    auto* SCS=BP->SimpleConstructionScript; auto* Root=SCS->CreateNode(USceneComponent::StaticClass(),TEXT("AssemblyRoot")); SCS->AddNode(Root); auto* Part=SCS->CreateNode(USkeletalMeshComponent::StaticClass(),TEXT("AssemblyMesh")); Root->AddChildNode(Part); auto* Comp=CastChecked<USkeletalMeshComponent>(Part->ComponentTemplate); Comp->SetSkeletalMesh(Mesh); Comp->SetAnimationMode(EAnimationMode::AnimationSingleNode); Comp->AnimationData.AnimToPlay=Anim; Comp->AnimationData.bSavedPlaying=false; Comp->AnimationData.bSavedLooping=false; Comp->SetCollisionEnabled(ECollisionEnabled::NoCollision); Comp->SetBoundsScale(6.f); Comp->VisibilityBasedAnimTickOption=EVisibilityBasedAnimTickOption::AlwaysTickPoseAndRefreshBones; Comp->bComponentUseFixedSkelBounds=false;
    auto Apply=Func(BP,TEXT("ApplyProgress"),true); auto Manual=Func(BP,TEXT("SetProgress"),true); auto Toggle=Func(BP,TEXT("ToggleAssembly")); Compile(BP);
    {
        auto* G=Apply.Graph; auto* Clamp=Math(G,TEXT("FClamp")); Wire(P(Apply.Entry,TEXT("Value")),P(Clamp,TEXT("Value"))); Def(Clamp,TEXT("Min"),TEXT("0")); Def(Clamp,TEXT("Max"),TEXT("1")); auto* SP=Set(G,TEXT("Progress")); Wire(Out(Clamp),P(SP,TEXT("Progress"))); Exec(Apply.Entry,SP);
        auto* Len=Call(G,UAnimSequenceBase::StaticClass(),TEXT("GetPlayLength")); Wire(Val(G,TEXT("ExplosionAnimation")),P(Len,UEdGraphSchema_K2::PN_Self)); auto* Mul=Math(G,TEXT("Multiply_FloatFloat")); Wire(Val(G,TEXT("Progress")),P(Mul,TEXT("A"))); Wire(Out(Len),P(Mul,TEXT("B"))); auto* Pos=Call(G,USkeletalMeshComponent::StaticClass(),TEXT("SetPosition")); Wire(Val(G,TEXT("AssemblyMesh")),P(Pos,UEdGraphSchema_K2::PN_Self)); Wire(Out(Mul),P(Pos,TEXT("InPos"))); Def(Pos,TEXT("bFireNotifies"),TEXT("false")); Exec(SP,Len); Exec(Len,Pos); Exec(Pos,Apply.Result);
    }
    {
        auto* G=Manual.Graph; auto* Stop=Set(G,TEXT("bAutoPlaying")); Def(Stop,TEXT("bAutoPlaying"),TEXT("false")); auto* C=Call(G,BP->GeneratedClass,TEXT("ApplyProgress")); Wire(P(Manual.Entry,TEXT("Value")),P(C,TEXT("Value"))); Exec(Manual.Entry,Stop); Exec(Stop,C); Exec(C,Manual.Result);
    }
    {
        auto* G=Toggle.Graph; auto* B=Branch(G,Val(G,TEXT("bAutoPlaying"))); Exec(Toggle.Entry,B); auto* Reverse=Math(G,TEXT("Subtract_FloatFloat")); Def(Reverse,TEXT("A"),TEXT("1")); Wire(Val(G,TEXT("TargetProgress")),P(Reverse,TEXT("B"))); auto* ST=Set(G,TEXT("TargetProgress")); Wire(Out(Reverse),P(ST,TEXT("TargetProgress"))); Exec(B,ST,UEdGraphSchema_K2::PN_Then);
        auto* IsFull=Math(G,TEXT("GreaterEqual_FloatFloat")); Wire(Val(G,TEXT("Progress")),P(IsFull,TEXT("A"))); Def(IsFull,TEXT("B"),TEXT("0.99999")); auto* Choose=Math(G,TEXT("SelectFloat")); Def(Choose,TEXT("A"),TEXT("0")); Def(Choose,TEXT("B"),TEXT("1")); Wire(Out(IsFull),P(Choose,TEXT("bPickA"))); auto* SM=Set(G,TEXT("TargetProgress")); Wire(Out(Choose),P(SM,TEXT("TargetProgress"))); Exec(B,SM,UEdGraphSchema_K2::PN_Else); auto* Play=Set(G,TEXT("bAutoPlaying")); Def(Play,TEXT("bAutoPlaying"),TEXT("true")); Exec(ST,Play); Exec(SM,Play); Exec(Play,Toggle.Result);
    }
    {
        auto* G=EventGraph(BP); LayoutIndex=0; auto* Begin=Event(G,AActor::StaticClass(),TEXT("ReceiveBeginPlay")); auto* SetAnim=Call(G,USkeletalMeshComponent::StaticClass(),TEXT("SetAnimation")); Wire(Val(G,TEXT("AssemblyMesh")),P(SetAnim,UEdGraphSchema_K2::PN_Self)); Wire(Val(G,TEXT("ExplosionAnimation")),P(SetAnim,TEXT("NewAnimToPlay"))); auto* Stop=Call(G,USkeletalMeshComponent::StaticClass(),TEXT("Stop")); Wire(Val(G,TEXT("AssemblyMesh")),P(Stop,UEdGraphSchema_K2::PN_Self)); auto* ApplyC=Call(G,BP->GeneratedClass,TEXT("ApplyProgress")); Wire(Val(G,TEXT("Progress")),P(ApplyC,TEXT("Value"))); Exec(Begin,SetAnim); Exec(SetAnim,Stop); Exec(Stop,ApplyC);
        auto* Tick=Event(G,AActor::StaticClass(),TEXT("ReceiveTick")); auto* B=Branch(G,Val(G,TEXT("bAutoPlaying"))); Exec(Tick,B); auto* Max=Math(G,TEXT("FMax")); Wire(Val(G,TEXT("FullTravelDuration")),P(Max,TEXT("A"))); Def(Max,TEXT("B"),TEXT("0.01")); auto* Speed=Math(G,TEXT("Divide_FloatFloat")); Def(Speed,TEXT("A"),TEXT("1")); Wire(Out(Max),P(Speed,TEXT("B"))); auto* Interp=Math(G,TEXT("FInterpTo_Constant")); Wire(Val(G,TEXT("Progress")),P(Interp,TEXT("Current"))); Wire(Val(G,TEXT("TargetProgress")),P(Interp,TEXT("Target"))); Wire(P(Tick,TEXT("DeltaSeconds")),P(Interp,TEXT("DeltaTime"))); Wire(Out(Speed),P(Interp,TEXT("InterpSpeed"))); auto* ApplyC2=Call(G,BP->GeneratedClass,TEXT("ApplyProgress")); Wire(Out(Interp),P(ApplyC2,TEXT("Value"))); Exec(B,ApplyC2); auto* Diff=Math(G,TEXT("NotEqual_FloatFloat")); Wire(Val(G,TEXT("Progress")),P(Diff,TEXT("A"))); Wire(Val(G,TEXT("TargetProgress")),P(Diff,TEXT("B"))); auto* Auto=Set(G,TEXT("bAutoPlaying")); Wire(Out(Diff),P(Auto,TEXT("bAutoPlaying"))); Exec(ApplyC2,Auto);
    }
    Compile(BP); auto* CDO=CastChecked<AActor>(BP->GeneratedClass->GetDefaultObject()); CDO->PrimaryActorTick.bCanEverTick=true; CDO->PrimaryActorTick.bStartWithTickEnabled=true; Save(BP); return BP;
}

static UK2Node_ComponentBoundEvent* BoundEvent(UWidgetBlueprint* BP,UEdGraph* G,FName WidgetName,UClass* WidgetClass,FName Delegate)
{
    auto* N=NewNode<UK2Node_ComponentBoundEvent>(G); auto* Property=FindFProperty<FObjectProperty>(BP->GeneratedClass,WidgetName); auto* DP=FindFProperty<FMulticastDelegateProperty>(WidgetClass,Delegate); check(Property&&DP); N->InitializeComponentBoundEventParams(Property,DP); N->AllocateDefaultPins(); return N;
}
static UWidgetBlueprint* MakeWidget(UBlueprint* Assembly)
{
    auto* BP=CastChecked<UWidgetBlueprint>(NewBP(TEXT("WBP_AssemblyControls"),UUserWidget::StaticClass(),true)); AddVar(BP,TEXT("Assembly"),ObjectT(Assembly->GeneratedClass));
    BP->WidgetTree=NewObject<UWidgetTree>(BP,TEXT("WidgetTree"),RF_Transactional); auto* Tree=BP->WidgetTree; auto* Canvas=Tree->ConstructWidget<UCanvasPanel>(UCanvasPanel::StaticClass(),TEXT("RootCanvas")); Tree->RootWidget=Canvas;
    auto* Border=Tree->ConstructWidget<UBorder>(UBorder::StaticClass(),TEXT("ControlPanel")); Border->SetBrushColor(FLinearColor(0.012f,0.021f,0.04f,0.94f)); Border->SetPadding(FMargin(26.f,20.f)); auto* CS=Canvas->AddChildToCanvas(Border); CS->SetAnchors(FAnchors(0.5f,1.f)); CS->SetAlignment(FVector2D(0.5f,1.f)); CS->SetPosition(FVector2D(0.f,-28.f)); CS->SetSize(FVector2D(760.f,160.f));
    auto* Box=Tree->ConstructWidget<UVerticalBox>(UVerticalBox::StaticClass(),TEXT("Controls")); Border->SetContent(Box);
    auto* Title=Tree->ConstructWidget<UTextBlock>(UTextBlock::StaticClass(),TEXT("Title")); Title->SetText(FText::FromString(TEXT("OPENMODEL  /  ASSEMBLY STUDY"))); auto Font=Title->Font; Font.Size=19; Title->SetFont(Font); Title->SetColorAndOpacity(FSlateColor(FLinearColor(0.83f,0.91f,1.f))); Box->AddChildToVerticalBox(Title)->SetPadding(FMargin(0,0,0,10));
    auto* Slider=Tree->ConstructWidget<USlider>(USlider::StaticClass(),TEXT("ProgressSlider")); Slider->bIsVariable=true; Slider->SetStepSize(0.001f); Slider->SetSliderBarColor(FLinearColor(0.18f,0.28f,0.4f)); Slider->SetSliderHandleColor(FLinearColor(0.25f,0.8f,1.f)); Box->AddChildToVerticalBox(Slider)->SetPadding(FMargin(0,0,0,12));
    auto* Button=Tree->ConstructWidget<UButton>(UButton::StaticClass(),TEXT("ToggleButton")); Button->bIsVariable=true; auto* BT=Tree->ConstructWidget<UTextBlock>(UTextBlock::StaticClass(),TEXT("ButtonLabel")); BT->SetText(FText::FromString(TEXT("EXPLODE / ASSEMBLE"))); BT->SetJustification(ETextJustify::Center); auto BF=BT->Font; BF.Size=16; BT->SetFont(BF); Button->SetContent(BT); Box->AddChildToVerticalBox(Button);
    auto Getter=Func(BP,TEXT("ReadProgress"),false,true); Getter.Entry->AddExtraFlags(FUNC_BlueprintPure|FUNC_Const); Compile(BP);
    auto* Read=Get(Getter.Graph,TEXT("Progress"),Assembly->GeneratedClass); Wire(Val(Getter.Graph,TEXT("Assembly")),P(Read,UEdGraphSchema_K2::PN_Self)); Wire(P(Read,TEXT("Progress")),P(Getter.Result,TEXT("ReturnValue"))); Exec(Getter.Entry,Getter.Result);
    FDelegateEditorBinding Binding; Binding.ObjectName=TEXT("ProgressSlider"); Binding.PropertyName=TEXT("Value"); Binding.FunctionName=TEXT("ReadProgress"); Binding.MemberGuid=Getter.Graph->GraphGuid; Binding.Kind=EBindingKind::Function; BP->Bindings.Add(Binding);
    auto* G=EventGraph(BP); LayoutIndex=0; auto* Construct=Event(G,UUserWidget::StaticClass(),TEXT("Construct")); auto* Find=Call(G,UGameplayStatics::StaticClass(),TEXT("GetActorOfClass")); Obj(Find,TEXT("ActorClass"),Assembly->GeneratedClass); auto* Cast=NewNode<UK2Node_DynamicCast>(G); Cast->TargetType=Assembly->GeneratedClass; Cast->AllocateDefaultPins(); Wire(Out(Find),Cast->GetCastSourcePin()); Exec(Construct,Find); Exec(Find,Cast); auto* SA=Set(G,TEXT("Assembly")); Wire(Cast->GetCastResultPin(),P(SA,TEXT("Assembly"))); Exec(Cast,SA);
    auto* Changed=BoundEvent(BP,G,TEXT("ProgressSlider"),USlider::StaticClass(),TEXT("OnValueChanged")); auto* SetP=Call(G,Assembly->GeneratedClass,TEXT("SetProgress")); Wire(Val(G,TEXT("Assembly")),P(SetP,UEdGraphSchema_K2::PN_Self)); Wire(P(Changed,TEXT("Value")),P(SetP,TEXT("Value"))); Exec(Changed,SetP);
    auto* Clicked=BoundEvent(BP,G,TEXT("ToggleButton"),UButton::StaticClass(),TEXT("OnClicked")); auto* Toggle=Call(G,Assembly->GeneratedClass,TEXT("ToggleAssembly")); Wire(Val(G,TEXT("Assembly")),P(Toggle,UEdGraphSchema_K2::PN_Self)); Exec(Clicked,Toggle); Compile(BP); Save(BP); return BP;
}

static UBlueprint* MakeUIHost(UWidgetBlueprint* Widget)
{
    auto* BP=NewBP(TEXT("BP_AssemblyUIHost"),AActor::StaticClass()); auto* G=EventGraph(BP); LayoutIndex=0; auto* Begin=Event(G,AActor::StaticClass(),TEXT("ReceiveBeginPlay")); auto* PC=Call(G,UGameplayStatics::StaticClass(),TEXT("GetPlayerController")); Def(PC,TEXT("PlayerIndex"),TEXT("0"));
    auto* Create=NewNode<UK2Node_CreateWidget>(G); Create->AllocateDefaultPins(); Obj(Create,TEXT("Class"),Widget->GeneratedClass); Create->PinDefaultValueChanged(P(Create,TEXT("Class"))); Wire(Out(PC),P(Create,TEXT("OwningPlayer"))); Exec(Begin,Create); auto* Add=Call(G,UUserWidget::StaticClass(),TEXT("AddToViewport")); Wire(Out(Create),P(Add,UEdGraphSchema_K2::PN_Self)); Exec(Create,Add);
    auto* Cursor=Set(G,TEXT("bShowMouseCursor"),APlayerController::StaticClass()); Wire(Out(PC),P(Cursor,UEdGraphSchema_K2::PN_Self)); Def(Cursor,TEXT("bShowMouseCursor"),TEXT("true")); Exec(Add,Cursor); auto* Mode=Call(G,UWidgetBlueprintLibrary::StaticClass(),TEXT("SetInputMode_GameAndUIEx")); Wire(Out(PC),P(Mode,TEXT("PlayerController"))); Wire(Out(Create),P(Mode,TEXT("InWidgetToFocus"))); Def(Mode,TEXT("bHideCursorDuringCapture"),TEXT("false")); Exec(Cursor,Mode); Compile(BP); Save(BP); return BP;
}

static void MakeMap(UBlueprint* Assembly,UBlueprint* UIHost,USkeletalMesh* Mesh)
{
    UWorld* World=GEditor->NewMap(); check(World); auto* Actor=World->SpawnActor<AActor>(Assembly->GeneratedClass,FVector::ZeroVector,FRotator::ZeroRotator); Actor->SetActorLabel(TEXT("Exploded Assembly")); World->SpawnActor<AActor>(UIHost->GeneratedClass);
    const FBoxSphereBounds Bounds=Mesh->GetBounds(); const FVector Center=Bounds.Origin; const float R=FMath::Max(Bounds.SphereRadius,40.f); auto* Camera=World->SpawnActor<ACameraActor>(); FVector Location=Center+FVector(5.f,3.f,3.f)*R; Camera->SetActorLocation(Location); Camera->SetActorRotation((Center-FVector(0,0,.3f)*R-Location).Rotation()); Camera->GetCameraComponent()->FieldOfView=43.f; Camera->SetActorLabel(TEXT("Assembly Camera")); if(auto* Prop=FindFProperty<FByteProperty>(ACameraActor::StaticClass(),TEXT("AutoActivateForPlayer")))Prop->SetPropertyValue_InContainer(Camera,1);
    auto* Key=World->SpawnActor<ADirectionalLight>(FVector::ZeroVector,FRotator(-35,-35,0)); Key->GetLightComponent()->SetIntensity(5.f); Key->SetActorLabel(TEXT("Key Light")); auto* Fill=World->SpawnActor<ADirectionalLight>(FVector::ZeroVector,FRotator(-25,130,0)); Fill->GetLightComponent()->SetIntensity(2.f); Fill->SetActorLabel(TEXT("Fill Light")); auto* Sky=World->SpawnActor<ASkyLight>(); Sky->GetLightComponent()->SetIntensity(0.5f);
    World->GetWorldSettings()->bForceNoPrecomputedLighting=true; FString MapFile=FPackageName::LongPackageNameToFilename(ContentRoot+TEXT("/Demo_ExplodedAssembly"),FPackageName::GetMapPackageExtension()); check(FEditorFileUtils::SaveMap(World,MapFile));
}
}

UOpenModelBuildCommandlet::UOpenModelBuildCommandlet() { IsClient=false; IsEditor=true; IsServer=false; LogToConsole=true; }
int32 UOpenModelBuildCommandlet::Main(const FString& Params)
{
    using namespace OpenModelBuild;
    FString Fbx, Name = TEXT("OpenModel");
    FParse::Value(*Params,TEXT("Fbx="),Fbx);
    FParse::Value(*Params,TEXT("Name="),Name);
    FParse::Value(*Params,TEXT("Root="),ContentRoot);
    if (!FPackageName::IsValidLongPackageName(ContentRoot)) return 2;
    const FString ModelPath = ContentRoot + TEXT("/Model");
    const FString MeshName = TEXT("SK_")+Name;
    if (!FParse::Param(*Params,TEXT("SkipImport")))
    {
        if(!FPaths::FileExists(Fbx)) { UE_LOG(LogTemp,Error,TEXT("Pass -Fbx=absolute FBX file")); return 2; }
        auto* Task=NewObject<UAssetImportTask>();
        Task->Filename=Fbx; Task->DestinationPath=ModelPath; Task->DestinationName=MeshName;
        Task->bAutomated=true; Task->bReplaceExisting=true; Task->bReplaceExistingSettings=true; Task->bSave=false;
        auto* Factory=NewObject<UFbxFactory>(); auto* UI=Factory->ImportUI;
        UI->bImportAsSkeletal=true; UI->MeshTypeToImport=FBXIT_SkeletalMesh; UI->OriginalImportType=FBXIT_SkeletalMesh;
        UI->bAutomatedImportShouldDetectType=false; UI->bImportAnimations=false;
        UI->bImportMaterials=true; UI->bImportTextures=true; UI->bCreatePhysicsAsset=false;
        UI->SkeletalMeshImportData->bConvertScene=true; UI->SkeletalMeshImportData->bConvertSceneUnit=true;
        UI->SkeletalMeshImportData->NormalImportMethod=FBXNIM_ImportNormalsAndTangents;
        UI->AnimSequenceImportData->bUseDefaultSampleRate=false; UI->AnimSequenceImportData->CustomSampleRate=30;
        Task->Factory=Factory; Task->Options=UI;
        FAssetToolsModule::GetModule().Get().ImportAssetTasks({Task});
        TArray<UObject*> Created;
        for (TObjectIterator<UObject> It; It; ++It)
            if (It->IsAsset() && It->GetPathName().StartsWith(ModelPath+TEXT("/"))) Created.Add(*It);
        for (UObject* Asset : Created) Save(Asset);
    }
    auto* Mesh=LoadObject<USkeletalMesh>(nullptr,*(ModelPath+TEXT("/")+MeshName+TEXT(".")+MeshName));
    if (Mesh && !FParse::Param(*Params,TEXT("SkipImport")))
    {
        auto* Task=NewObject<UAssetImportTask>(); Task->Filename=Fbx;
        Task->DestinationPath=ModelPath; Task->DestinationName=MeshName+TEXT("_Anim");
        Task->bAutomated=true; Task->bReplaceExisting=true; Task->bReplaceExistingSettings=true; Task->bSave=false;
        auto* Factory=NewObject<UFbxFactory>(); auto* UI=Factory->ImportUI;
        UI->bImportMesh=false; UI->bImportAnimations=true; UI->Skeleton=Mesh->Skeleton;
        UI->MeshTypeToImport=FBXIT_Animation; UI->OriginalImportType=FBXIT_Animation;
        UI->bAutomatedImportShouldDetectType=false;
        UI->AnimSequenceImportData->bUseDefaultSampleRate=false; UI->AnimSequenceImportData->CustomSampleRate=30;
        UI->AnimSequenceImportData->bConvertScene=true; UI->AnimSequenceImportData->bConvertSceneUnit=true;
        Task->Factory=Factory; Task->Options=UI;
        FAssetToolsModule::GetModule().Get().ImportAssetTasks({Task});
        for (auto* Object : Task->Result) { UE_LOG(LogTemp,Display,TEXT("ANIMATION_IMPORT %s"),*Object->GetPathName()); Save(Object); }
    }
    auto* Anim=LoadObject<UAnimSequence>(nullptr,*(ModelPath+TEXT("/")+MeshName+TEXT("_Anim.")+MeshName+TEXT("_Anim")));
    if(!Mesh||!Anim) { UE_LOG(LogTemp,Error,TEXT("Import did not produce mesh and animation")); return 3; }
    // Keep rigid part tracks at full float precision, including the FBX root rotation.
    const FString CompressionPath = ContentRoot + TEXT("/AssemblyCompression");
    auto* Compression = LoadObject<UAnimBoneCompressionSettings>(nullptr, *(CompressionPath+TEXT(".AssemblyCompression")));
    if (!Compression)
    {
        Compression = NewObject<UAnimBoneCompressionSettings>(CreatePackage(*CompressionPath), TEXT("AssemblyCompression"), RF_Public|RF_Standalone);
        FAssetRegistryModule::AssetCreated(Compression);
    }
    auto* Codec = Compression->Codecs.Num() ? Cast<UAnimCompress_BitwiseCompressOnly>(Compression->Codecs[0]) : nullptr;
    if (!Codec) Codec = NewObject<UAnimCompress_BitwiseCompressOnly>(Compression, TEXT("RigidFullPrecision"));
    Codec->TranslationCompressionFormat = ACF_None;
    Codec->RotationCompressionFormat = ACF_None;
    Codec->ScaleCompressionFormat = ACF_None;
    Compression->Codecs = {Codec};
    Compression->ErrorThreshold = .001f;
    Compression->bForceBelowThreshold = true;
    Save(Compression);
    Anim->BoneCompressionSettings = Compression;
    Anim->MarkRawDataAsModified();
    Anim->RequestSyncAnimRecompression();
    Save(Anim);
    if (!FParse::Param(*Params,TEXT("ImportOnly")))
    {
        auto* Assembly=MakeAssembly(Mesh,Anim); auto* Widget=MakeWidget(Assembly);
        auto* Host=MakeUIHost(Widget); MakeMap(Assembly,Host,Mesh);
    }
    UE_LOG(LogTemp,Display,TEXT("OPENMODEL_BUILD_OK Mesh=%s Animation=%s Duration=%g Bones=%d"),*Mesh->GetPathName(),*Anim->GetPathName(),Anim->SequenceLength,Mesh->RefSkeleton.GetNum());
    return 0;
}
#include "OpenModelPackage.inl"
#include "OpenModelNoUMG.inl"
