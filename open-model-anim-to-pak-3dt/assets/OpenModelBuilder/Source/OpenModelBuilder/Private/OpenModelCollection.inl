#include "Engine/LevelScriptActor.h"
#include "Components/BoxComponent.h"
#include "AssetRegistryModule.h"
#include "IAssetRegistry.h"
#include "DesktopPlatformModule.h"
#include "IDesktopPlatform.h"
namespace OpenModelBuild
{
static int32 FixInputDefaults(const FString& Params)
{
    auto* BP=LoadObject<UBlueprint>(nullptr,*OpenModelLiteral(TEXT("/Game/OpenModel/NoUMG/BP_ExplodedAssembly_NoUMG.BP_ExplodedAssembly_NoUMG")));check(BP);
    check(CastChecked<AActor>(BP->GeneratedClass->GetDefaultObject())->AutoReceiveInput==EAutoReceiveInput::Player0);
    for(const TCHAR* Suffix:{TEXT("/NoUMG/Demo_ExplodedAssembly_NoUMG"),TEXT("/NoUMG/Demo_ExplodedAssembly_FreeView")})
    {
        const FString Map=OpenModelRoot()+Suffix;check(FEditorFileUtils::LoadMap(Map,false,true));
        auto* World=GEditor->GetEditorWorldContext().World();int32 Fixed=0;
        for(TActorIterator<AActor> It(World);It;++It)if(It->GetClass()==BP->GeneratedClass)
        {
            UE_LOG(LogTemp,Display,TEXT("INSTANCE_INPUT_BEFORE %s receive=%d"),*It->GetPathName(),int32(It->AutoReceiveInput));
            It->AutoReceiveInput=EAutoReceiveInput::Player0;It->SetActorTickEnabled(true);It->MarkPackageDirty();++Fixed;
        }
        check(Fixed==1);check(FEditorFileUtils::SaveMap(World,FPackageName::LongPackageNameToFilename(Map,FPackageName::GetMapPackageExtension())));
    }
    UE_LOG(LogTemp,Display,TEXT("INSTANCE_INPUT_FIXED"));return 0;
}
static int32 PrepareCollectionPackage(const FString& Params)
{
    FString Destination,Report;FParse::Value(*Params,TEXT("Destination="),Destination);FParse::Value(*Params,TEXT("Report="),Report);
    FString ObjectIds,CollectionFile;FParse::Value(*Params,TEXT("ObjectIds="),ObjectIds);FParse::Value(*Params,TEXT("CollectionManifest="),CollectionFile);
    TArray<FString> SelectedIds;ObjectIds.ParseIntoArray(SelectedIds,TEXT("+"),true);
    TMap<FString,FString> BlueprintOverrides;
    if(!CollectionFile.IsEmpty())
    {
        FString Text;TSharedPtr<FJsonObject> Config;
        check(FFileHelper::LoadFileToString(Text,*CollectionFile));check(FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text),Config));
        for(const auto& Value:Config->GetArrayField(TEXT("objects")))
        {
            auto Object=Value->AsObject();const FString Id=Object->GetStringField(TEXT("objectId"));
            check(!BlueprintOverrides.Contains(Id));BlueprintOverrides.Add(Id,Object->GetStringField(TEXT("blueprint")));
            if(ObjectIds.IsEmpty())SelectedIds.Add(Id);
        }
    }
    check(SelectedIds.Num()>0);
    TSet<FName> UniqueIds;for(const auto& Id:SelectedIds){check(OpenModelValidId(Id));check(!UniqueIds.Contains(*Id));UniqueIds.Add(*Id);}
    check(FPaths::DirectoryExists(Destination));
    FString MapName=TEXT("/Game/OpenModelCollection/Demo_ExplodedAssembly_FreeView");FParse::Value(*Params,TEXT("Map="),MapName);
    check(FPackageName::IsValidLongPackageName(MapName) && MapName.StartsWith(TEXT("/Game/")));
    auto* World=GEditor->NewMap();check(World);TArray<AActor*> Keep;
    TArray<TSharedPtr<FJsonValue>> ActorReports;TArray<FName> BlueprintPackages;float Cursor=0.f;
    for(const FString& Id:SelectedIds)
    {
        FString Path=BlueprintOverrides.Contains(Id)?BlueprintOverrides[Id]:OpenModelLibraryBlueprint(Id);
        auto* BP=LoadObject<UBlueprint>(nullptr,*Path);
        if(!BP && !BlueprintOverrides.Contains(Id)){Path=FString(TEXT("/Game/"))+Id+TEXT("/NoUMG/BP_ExplodedAssembly_NoUMG.BP_ExplodedAssembly_NoUMG");BP=LoadObject<UBlueprint>(nullptr,*Path);}
        check(BP && BP->FunctionGraphs.Num()==2);BlueprintPackages.Add(BP->GetOutermost()->GetFName());
        auto* Actor=World->SpawnActor<AActor>(BP->GeneratedClass);check(Actor);Actor->AutoReceiveInput=EAutoReceiveInput::Player0;
        auto* Box=Actor->FindComponentByClass<UBoxComponent>();checkf(Box,TEXT("Collection spacing requires a verified full-motion box: %s"),*Id);
        const FBox Bounds=Box->Bounds.GetBox();
        const FVector Position(Keep.Num()?Cursor-Bounds.Min.X:0.f,0.f,0.f);
        Actor->SetActorLocation(Position);Actor->SetActorLabel(FString(TEXT("OpenModel_"))+Id);
        Cursor=Position.X+Bounds.Max.X+FMath::Max(100.f,Bounds.GetSize().GetMax()*.15f);
        Keep.Add(Actor);auto Row=MakeShared<FJsonObject>();Row->SetStringField(TEXT("objectId"),Id);
        Row->SetStringField(TEXT("blueprint"),Path);Row->SetStringField(TEXT("location"),Position.ToString());
        ActorReports.Add(MakeShared<FJsonValueObject>(Row));
    }
    TArray<AActor*> Remove;for(TActorIterator<AActor> It(World);It;++It)
        if(!Keep.Contains(*It) && !It->IsA<AWorldSettings>() && !It->IsA<ALevelScriptActor>())Remove.Add(*It);
    for(auto* Actor:Remove)World->DestroyActor(Actor);
    check(FEditorFileUtils::SaveMap(World,FPackageName::LongPackageNameToFilename(MapName,FPackageName::GetMapPackageExtension())));
    int32 Count=0;for(TActorIterator<AActor> It(World);It;++It)if(!It->IsA<AWorldSettings>()&&!It->IsA<ALevelScriptActor>())++Count;
    check(Count==SelectedIds.Num());
    auto& Registry=FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry")).Get();Registry.SearchAllAssets(true);
    TArray<FString> ScanPaths{TEXT("/Game"),TEXT("/JC_CustomAssets")};
    Registry.ScanPathsSynchronous(ScanPaths,true);
    TArray<FName> Queue;Queue.Add(*MapName);Queue.Append(BlueprintPackages);
    TSet<FName> Seen;TArray<TSharedPtr<FJsonValue>> Copied,PluginDependencies;
    for(int32 I=0;I<Queue.Num();++I)
    {
        const FName Package=Queue[I];if(Seen.Contains(Package))continue;Seen.Add(Package);
        const bool IsGame=Package.ToString().StartsWith(TEXT("/Game/"));
        const bool IsLibrary=Package.ToString().StartsWith(TEXT("/JC_CustomAssets/"));if(!IsGame&&!IsLibrary)continue;
        FString File;check(FPackageName::DoesPackageExist(Package.ToString(),nullptr,&File));
        TArray<FName> Dependencies;Registry.GetDependencies(Package,Dependencies);Queue.Append(Dependencies);
        if(IsLibrary){PluginDependencies.Add(MakeShared<FJsonValueString>(Package.ToString().Mid(17)+FPaths::GetExtension(File,true)));continue;}
        const FString Relative=Package.ToString().Mid(6)+FPaths::GetExtension(File,true);
        const FString Target=FPaths::Combine(Destination,Relative);
        checkf(!FPaths::FileExists(Target),TEXT("Target asset collision: %s"),*Target);
        IFileManager::Get().MakeDirectory(*FPaths::GetPath(Target),true);check(IFileManager::Get().Copy(*Target,*File)==COPY_OK);
        Copied.Add(MakeShared<FJsonValueString>(Relative));
    }
    check(Copied.Num()+PluginDependencies.Num()>1+SelectedIds.Num());
    auto Manifest=MakeShared<FJsonObject>();Manifest->SetArrayField(TEXT("actors"),ActorReports);Manifest->SetArrayField(TEXT("migratedFiles"),Copied);Manifest->SetArrayField(TEXT("pluginDependencies"),PluginDependencies);
    FString CustomRoot,TargetProject;FParse::Value(*Params,TEXT("CustomRoot="),CustomRoot);FParse::Value(*Params,TEXT("TargetProject="),TargetProject);
    if(!CustomRoot.IsEmpty()&&!TargetProject.IsEmpty())
    {
        FString Identifier;auto* Desktop=FDesktopPlatformModule::Get();check(Desktop->RegisterEngineInstallation(CustomRoot,Identifier));
        check(Desktop->SetEngineIdentifierForProject(TargetProject,Identifier));Manifest->SetStringField(TEXT("engineIdentifier"),Identifier);
        Manifest->SetStringField(TEXT("engineRoot"),CustomRoot);
    }
    Manifest->SetNumberField(TEXT("businessActorCount"),Count);Manifest->SetStringField(TEXT("map"),MapName);
    Manifest->SetStringField(TEXT("placement"),TEXT("Separate world-X positions derived from full-motion collision bounds; Blueprint local origins and source scales unchanged"));
    FString Json;FJsonSerializer::Serialize(Manifest,TJsonWriterFactory<>::Create(&Json));check(FFileHelper::SaveStringToFile(Json,*Report));
    UE_LOG(LogTemp,Display,TEXT("COLLECTION_PREPARED actors=%d assets=%d"),Count,Copied.Num());return 0;
}
}
