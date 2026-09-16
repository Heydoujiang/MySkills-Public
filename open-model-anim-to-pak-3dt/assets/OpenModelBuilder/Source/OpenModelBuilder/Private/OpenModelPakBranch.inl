#include "Materials/MaterialInterface.h"
#include "Materials/MaterialFunctionInterface.h"
#include "Materials/MaterialParameterCollection.h"
#include "Engine/Texture.h"
#include "Engine/Texture2D.h"
#include "Animation/Skeleton.h"
#include "Factories/TextureFactory.h"
#include "PhysicsEngine/PhysicsAsset.h"
namespace OpenModelBuild
{
static int32 PreparePakBranch(const FString& Params)
{
    FString Destination,Thumbnail,Report,TargetProject,CustomRoot;
    FParse::Value(*Params,TEXT("Destination="),Destination);FParse::Value(*Params,TEXT("Thumbnail="),Thumbnail);
    FParse::Value(*Params,TEXT("Report="),Report);FParse::Value(*Params,TEXT("TargetProject="),TargetProject);FParse::Value(*Params,TEXT("CustomRoot="),CustomRoot);
    check(FPaths::DirectoryExists(Destination));
    const FString Source=TEXT("/Game/OpenModel/NoUMG/BP_ExplodedAssembly_NoUMG");
    const FString Root=TEXT("/JC_CustomAssets/ObjectLibrary");
    auto& Registry=FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry")).Get();Registry.SearchAllAssets(true);
    TSet<FName> Seen;TArray<FName> Queue;Queue.Add(*Source);TArray<UObject*> Assets;TArray<FAssetRenameData> Renames;
    auto Manifest=MakeShared<FJsonObject>();TArray<TSharedPtr<FJsonValue>> Records;
    for(int32 I=0;I<Queue.Num();++I)
    {
        const FName Package=Queue[I];if(Seen.Contains(Package))continue;Seen.Add(Package);if(!Package.ToString().StartsWith(TEXT("/Game/")))continue;
        TArray<FAssetData> Data;Registry.GetAssetsByPackageName(Package,Data);check(Data.Num()==1);
        auto* Asset=Data[0].GetAsset();check(Asset && !Asset->IsA<UWorld>());Assets.Add(Asset);
        FString Folder,Name=Asset->GetName();
        if(Package.ToString()==Source){Folder=Root+TEXT("/Exhibition/OpenModel/OpenModel_Gun");Name=TEXT("OpenModel_Gun");}
        else if(Asset->IsA<UMaterialInterface>() || Asset->IsA<UMaterialFunctionInterface>() || Asset->IsA<UMaterialParameterCollection>())Folder=Root+TEXT("/Material");
        else if(Asset->IsA<UTexture>())Folder=Root+TEXT("/Texture");
        else if(Asset->IsA<USkeletalMesh>() || Asset->IsA<UStaticMesh>() || Asset->IsA<USkeleton>() || Asset->IsA<UAnimationAsset>() || Asset->IsA<UAnimBoneCompressionSettings>() || Asset->IsA<UPhysicsAsset>())Folder=Root+TEXT("/StaticMesh");
        else {UE_LOG(LogTemp,Fatal,TEXT("Unclassified dependency: %s (%s)"),*Asset->GetPathName(),*Asset->GetClass()->GetName());}
        check(!FPackageName::DoesPackageExist(Folder+TEXT("/")+Name));Renames.Emplace(Asset,Folder,Name);
        auto Record=MakeShared<FJsonObject>();Record->SetStringField(TEXT("source"),Package.ToString());Record->SetStringField(TEXT("target"),Folder+TEXT("/")+Name);Record->SetStringField(TEXT("class"),Asset->GetClass()->GetName());Records.Add(MakeShared<FJsonValueObject>(Record));
        TArray<FName> Dependencies;Registry.GetDependencies(Package,Dependencies);Queue.Append(Dependencies);
    }
    auto& AssetTools=FAssetToolsModule::GetModule().Get();check(AssetTools.RenameAssets(Renames));
    for(auto* Asset:Assets){if(auto* BP=Cast<UBlueprint>(Asset))Compile(BP);Save(Asset);}
    auto* Task=NewObject<UAssetImportTask>();Task->Filename=Thumbnail;Task->DestinationPath=Root+TEXT("/Exhibition/OpenModel/OpenModel_Gun");Task->DestinationName=TEXT("OpenModel_Gun_T");Task->bAutomated=true;Task->bSave=true;Task->Factory=NewObject<UTextureFactory>();AssetTools.ImportAssetTasks({Task});
    auto* Texture=LoadObject<UTexture2D>(nullptr,*(Root+TEXT("/Exhibition/OpenModel/OpenModel_Gun/OpenModel_Gun_T.OpenModel_Gun_T")));check(Texture);
    Texture->LODGroup=TEXTUREGROUP_UI;Texture->NeverStream=true;Texture->PostEditChange();Save(Texture);Assets.Add(Texture);
    // Save after rename through UE, then copy only final assets; never migrate maps or redirectors.
    TArray<TSharedPtr<FJsonValue>> Copied;
    for(auto* Asset:Assets)
    {
        const FString Package=Asset->GetOutermost()->GetName();check(Package.StartsWith(TEXT("/JC_CustomAssets/")));
        const FString Relative=Package.Mid(FString(TEXT("/JC_CustomAssets/")).Len())+TEXT(".uasset");
        const FString File=FPackageName::LongPackageNameToFilename(Package,TEXT(".uasset"));const FString Target=FPaths::Combine(Destination,Relative);
        checkf(!FPaths::FileExists(Target),TEXT("Target collision: %s"),*Target);IFileManager::Get().MakeDirectory(*FPaths::GetPath(Target),true);check(IFileManager::Get().Copy(*Target,*File)==COPY_OK);Copied.Add(MakeShared<FJsonValueString>(Relative));
    }
    FString Identifier;auto* Desktop=FDesktopPlatformModule::Get();check(Desktop->RegisterEngineInstallation(CustomRoot,Identifier));check(Desktop->SetEngineIdentifierForProject(TargetProject,Identifier));
    Manifest->SetArrayField(TEXT("relocations"),Records);Manifest->SetArrayField(TEXT("copiedAssets"),Copied);Manifest->SetStringField(TEXT("engineIdentifier"),Identifier);Manifest->SetStringField(TEXT("thumbnailSource"),Thumbnail);Manifest->SetBoolField(TEXT("mapsMigrated"),false);
    FString Json;FJsonSerializer::Serialize(Manifest,TJsonWriterFactory<>::Create(&Json));check(FFileHelper::SaveStringToFile(Json,*Report));
    UE_LOG(LogTemp,Display,TEXT("PAK_BRANCH_PREPARED assets=%d maps=0"),Assets.Num());return 0;
}
}
