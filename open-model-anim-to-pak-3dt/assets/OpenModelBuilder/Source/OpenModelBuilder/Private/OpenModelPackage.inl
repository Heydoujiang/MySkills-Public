#include "AssetRegistryModule.h"
#include "IAssetRegistry.h"
#include "DesktopPlatformModule.h"
#include "IDesktopPlatform.h"
#include "Engine/LevelScriptActor.h"
#include "Engine/Brush.h"
namespace OpenModelBuild
{
static int32 PreparePackage(const FString& Params)
{
    FString Destination, Report, CustomRoot, TargetProject;
    FParse::Value(*Params,TEXT("Destination="),Destination);
    FParse::Value(*Params,TEXT("Report="),Report);
    FParse::Value(*Params,TEXT("CustomRoot="),CustomRoot);
    FParse::Value(*Params,TEXT("TargetProject="),TargetProject);
    check(!Destination.IsEmpty() && FPaths::DirectoryExists(Destination));
    auto* BP=LoadObject<UBlueprint>(nullptr,TEXT("/Game/OpenModel/NoUMG/BP_ExplodedAssembly_NoUMG.BP_ExplodedAssembly_NoUMG"));check(BP);
    UEdGraph* Graph=nullptr; UK2Node_FunctionEntry* Entry=nullptr;
    for(auto* G:BP->FunctionGraphs)if(G->GetFName()==TEXT("ControlAssembly"))Graph=G;
    check(Graph);for(auto* Node:Graph->Nodes)if(auto* E=Cast<UK2Node_FunctionEntry>(Node))Entry=E;check(Entry);
    TArray<FBPVariableDescription> Exposed;
    for(const auto& V:BP->NewVariables)if((V.PropertyFlags&CPF_Edit) && !(V.PropertyFlags&CPF_DisableEditOnInstance))Exposed.Add(V);
    // Packaging validates the latest two-function contract without editing the Blueprint.
    check(BP->FunctionGraphs.Num()==2);
    check(!Entry->FindPin(TEXT("Progress")) && !Entry->FindPin(TEXT("NewProgress")) && !Entry->FindPin(TEXT("SetProgress")));
    check(P(Entry,UEdGraphSchema_K2::PN_Then)->LinkedTo.Num()==1);
    check(P(Entry,UEdGraphSchema_K2::PN_Then)->LinkedTo[0]->GetOwningNode()->IsA<UK2Node_IfThenElse>());
    UK2Node_FunctionEntry* UserEntry=nullptr;
    for(auto* G:BP->FunctionGraphs)if(G->GetFName()==TEXT("UserVariable"))
        for(auto* N:G->Nodes)if(auto* E=Cast<UK2Node_FunctionEntry>(N))UserEntry=E;
    check(UserEntry);
    auto Manifest=MakeShared<FJsonObject>();TArray<TSharedPtr<FJsonValue>> Inputs;
    auto* Cursor=P(UserEntry,UEdGraphSchema_K2::PN_Then);
    for(const auto& V:Exposed)
    {
        check(Cursor->LinkedTo.Num()==1);
        auto* Setter=Cast<UK2Node_VariableSet>(Cursor->LinkedTo[0]->GetOwningNode());
        check(Setter && Setter->VariableReference.GetMemberName()==V.VarName);
        check(P(Setter,V.VarName)->LinkedTo.Contains(P(UserEntry,V.VarName)));
        Inputs.Add(MakeShared<FJsonValueString>(V.VarName.ToString()));Cursor=P(Setter,UEdGraphSchema_K2::PN_Then);
    }
    Manifest->SetArrayField(TEXT("userVariableSetterOrder"),Inputs);
    Manifest->SetBoolField(TEXT("blueprintUnmodified"),true);
    FString MapName=TEXT("/Game/OpenModel/NoUMG/Demo_ExplodedAssembly_FreeView");
    FParse::Value(*Params,TEXT("Map="),MapName);
    check(FEditorFileUtils::LoadMap(MapName,false,true));auto* World=GEditor->GetEditorWorldContext().World();
    TArray<AActor*> Remove;AActor* Assembly=nullptr;
    for(TActorIterator<AActor> It(World);It;++It)
    {
        if(It->GetClass()==BP->GeneratedClass){check(!Assembly);Assembly=*It;}
        else if(!It->IsA<AWorldSettings>() && !It->IsA<ALevelScriptActor>())Remove.Add(*It);
    }
    check(Assembly);for(auto* Actor:Remove)World->DestroyActor(Actor);
    Assembly->SetActorLocation(FVector::ZeroVector);Assembly->SetActorLabel(TEXT("BP_ExplodedAssembly_NoUMG"));
    check(FEditorFileUtils::SaveMap(World,FPackageName::LongPackageNameToFilename(MapName,FPackageName::GetMapPackageExtension())));
    int32 UserActors=0;for(TActorIterator<AActor> It(World);It;++It)if(!It->IsA<AWorldSettings>() && !It->IsA<ALevelScriptActor>())++UserActors;
    check(UserActors==1);Manifest->SetNumberField(TEXT("packagedActorCount"),UserActors);
    Manifest->SetStringField(TEXT("actorLocation"),Assembly->GetActorLocation().ToString());
    // Resolve the same package dependency graph used by asset migration, keeping /Game paths.
    auto& Registry=FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry")).Get();
    Registry.SearchAllAssets(true);
    TSet<FName> Seen;TArray<FName> Queue;Queue.Add(*MapName);
    TArray<TSharedPtr<FJsonValue>> Copied;
    for(int32 I=0;I<Queue.Num();++I)
    {
        FName Package=Queue[I];if(Seen.Contains(Package))continue;Seen.Add(Package);
        if(!Package.ToString().StartsWith(TEXT("/Game/")))continue;
        FString Filename;check(FPackageName::DoesPackageExist(Package.ToString(),nullptr,&Filename));
        FString Relative=Package.ToString().Mid(6)+FPaths::GetExtension(Filename,true);
        FString Target=FPaths::Combine(Destination,Relative);IFileManager::Get().MakeDirectory(*FPaths::GetPath(Target),true);
        check(IFileManager::Get().Copy(*Target,*Filename,true,true)==COPY_OK);
        Copied.Add(MakeShared<FJsonValueString>(Relative));
        TArray<FName> Dependencies;Registry.GetDependencies(Package,Dependencies);Queue.Append(Dependencies);
    }
    Manifest->SetArrayField(TEXT("migratedFiles"),Copied);
    if(!CustomRoot.IsEmpty() && !TargetProject.IsEmpty())
    {
        FString Identifier;auto* Desktop=FDesktopPlatformModule::Get();
        check(Desktop->RegisterEngineInstallation(CustomRoot,Identifier));
        check(Desktop->SetEngineIdentifierForProject(TargetProject,Identifier));
        Manifest->SetStringField(TEXT("engineIdentifier"),Identifier);Manifest->SetStringField(TEXT("engineRoot"),CustomRoot);
    }
    FString Json;FJsonSerializer::Serialize(Manifest,TJsonWriterFactory<>::Create(&Json));
    check(FFileHelper::SaveStringToFile(Json,*Report));
    UE_LOG(LogTemp,Display,TEXT("PACKAGE_PREP_OK inputs=%d actors=%d migrated=%d"),Exposed.Num(),UserActors,Copied.Num());return 0;
}
}
