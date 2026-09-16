#include "Components/BoxComponent.h"
#include "Components/StaticMeshComponent.h"
#include "PhysicsEngine/BodySetup.h"
#include "PhysicsEngine/PhysicsAsset.h"
#include "Rendering/SkeletalMeshRenderData.h"
#include "Rendering/SkeletalMeshLODRenderData.h"
namespace OpenModelBuild
{
static int32 EnsureMotionCollision(const FString& Params)
{
    FString Path,Report,Profile=TEXT("BlockAllDynamic");float SampleRate=120.f,Margin=1.f;
    FParse::Value(*Params,TEXT("Blueprint="),Path);FParse::Value(*Params,TEXT("Report="),Report);
    FParse::Value(*Params,TEXT("CollisionProfile="),Profile);FParse::Value(*Params,TEXT("SampleRate="),SampleRate);FParse::Value(*Params,TEXT("MarginCm="),Margin);
    check(!Path.IsEmpty() && !Report.IsEmpty() && SampleRate>=30.f && Margin>=0.f);
    auto* BP=LoadObject<UBlueprint>(nullptr,*Path);check(BP && BP->SimpleConstructionScript);
    TGuardValue<bool> Guard(GAllowActorScriptExecutionInEditor,true);
    auto* World=GEditor->NewMap();auto* Actor=World->SpawnActor<AActor>(BP->GeneratedClass);
    TArray<UPrimitiveComponent*> Components;Actor->GetComponents(Components);bool Existing=false;
    for(auto* C:Components)
    {
        if(C->GetCollisionEnabled()==ECollisionEnabled::NoCollision)continue;
        if(auto* Skel=Cast<USkeletalMeshComponent>(C))Existing|=Skel->GetPhysicsAsset() && Skel->GetPhysicsAsset()->SkeletalBodySetups.Num()>0;
        else if(auto* Body=C->GetBodySetup())Existing|=Body->AggGeom.GetElementCount()>0 || Body->CollisionTraceFlag==CTF_UseComplexAsSimple;
    }
    auto Result=MakeShared<FJsonObject>();Result->SetBoolField(TEXT("existingCollision"),Existing);
    if(!Existing)
    {
        auto* Mesh=Actor->FindComponentByClass<USkeletalMeshComponent>();check(Mesh && Mesh->SkeletalMesh);
        auto* Prop=FindFProperty<FObjectProperty>(Actor->GetClass(),TEXT("ExplosionAnimation"));check(Prop);
        auto* Anim=Cast<UAnimSequence>(Prop->GetObjectPropertyValue_InContainer(Actor));check(Anim);
        Mesh->SetAnimationMode(EAnimationMode::AnimationSingleNode);Mesh->SetAnimation(Anim);Mesh->Stop();
        auto* Render=Mesh->SkeletalMesh->GetResourceForRendering();check(Render && Render->LODRenderData.Num());
        auto& LOD=Render->LODRenderData[0];auto* Weights=Mesh->GetSkinWeightBuffer(0);check(Weights);
        const FTransform ToRoot=Mesh->GetComponentTransform().GetRelativeTransform(Actor->GetRootComponent()->GetComponentTransform());
        FBox Bounds(ForceInit);const int32 Steps=FMath::Max(1,FMath::CeilToInt(Anim->SequenceLength*SampleRate));
        for(int32 I=0;I<=Steps;++I)
        {
            Mesh->SetPosition(Anim->SequenceLength*float(I)/Steps,false);Mesh->TickAnimation(0.f,false);Mesh->RefreshBoneTransforms();
            TArray<FMatrix> Matrices;Mesh->CacheRefToLocalMatrices(Matrices);TArray<FVector> Positions;
            USkinnedMeshComponent::ComputeSkinnedPositions(Mesh,Positions,Matrices,LOD,*Weights);
            check(Positions.Num()>0);for(const auto& V:Positions)Bounds+=ToRoot.TransformPosition(V);
        }
        // Margin handles between-sample motion; curved/fast paths require denser sampling and visual review.
        Bounds=Bounds.ExpandBy(Margin+Bounds.GetExtent().GetMax()*.01f);check(Bounds.IsValid);
        auto* Node=BP->SimpleConstructionScript->CreateNode(UBoxComponent::StaticClass(),TEXT("AssemblyMotionCollision"));
        check(BP->SimpleConstructionScript->GetRootNodes().Num()==1);BP->SimpleConstructionScript->GetRootNodes()[0]->AddChildNode(Node);
        auto* Box=CastChecked<UBoxComponent>(Node->ComponentTemplate);Box->SetBoxExtent(Bounds.GetExtent());Box->SetRelativeLocation(Bounds.GetCenter());Box->SetCollisionProfileName(*Profile);Box->SetCollisionEnabled(ECollisionEnabled::QueryAndPhysics);
        Compile(BP);Save(BP);
        Result->SetStringField(TEXT("centerRootSpace"),Bounds.GetCenter().ToString());Result->SetStringField(TEXT("extentRootSpace"),Bounds.GetExtent().ToString());Result->SetNumberField(TEXT("samples"),Steps+1);Result->SetStringField(TEXT("profile"),Profile);
        auto* Probe=World->SpawnActor<AActor>(BP->GeneratedClass);auto* ProbeBox=Probe->FindComponentByClass<UBoxComponent>();check(ProbeBox && ProbeBox->GetCollisionEnabled()!=ECollisionEnabled::NoCollision);
        check(ProbeBox->GetUnscaledBoxExtent().Equals(Bounds.GetExtent(),.001f));
        FHitResult Hit;const FVector Center=ProbeBox->GetComponentLocation();const float Distance=Bounds.GetExtent().Size()+100.f;
        const bool HitBox=World->LineTraceSingleByChannel(Hit,Center+FVector(Distance,0,0),Center-FVector(Distance,0,0),ECC_Visibility);
        check(HitBox && Hit.GetActor()==Probe);Result->SetBoolField(TEXT("visibilityTracePassed"),true);
    }
    Result->SetBoolField(TEXT("addedBox"),!Existing);FString Json;FJsonSerializer::Serialize(Result,TJsonWriterFactory<>::Create(&Json));check(FFileHelper::SaveStringToFile(Json,*Report));
    UE_LOG(LogTemp,Display,TEXT("MOTION_COLLISION_OK added=%d"),!Existing);return 0;
}
}
