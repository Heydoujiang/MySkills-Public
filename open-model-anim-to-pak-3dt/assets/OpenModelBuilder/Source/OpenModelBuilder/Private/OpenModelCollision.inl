#include "OpenModelAssetContext.h"
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
    bool Added=false,Passed=false;
    {
        TArray<USkeletalMeshComponent*> Meshes;Actor->GetComponents(Meshes);check(Meshes.Num());
        auto* Prop=FindFProperty<FObjectProperty>(Actor->GetClass(),TEXT("ExplosionAnimation"));check(Prop);
        auto* Anim=Cast<UAnimSequence>(Prop->GetObjectPropertyValue_InContainer(Actor));check(Anim);
        FBox Bounds(ForceInit);uint64 TotalVertices=0;
        const int32 Steps=FMath::Max(1,FMath::CeilToInt(Anim->SequenceLength*SampleRate));
        for(auto* Mesh:Meshes)
        {
            check(Mesh->SkeletalMesh);Mesh->SetAnimationMode(EAnimationMode::AnimationSingleNode);Mesh->SetAnimation(Anim);Mesh->Stop();
            auto* Render=Mesh->SkeletalMesh->GetResourceForRendering();check(Render && Render->LODRenderData.Num());
            auto& LOD=Render->LODRenderData[0];auto* Weights=Mesh->GetSkinWeightBuffer(0);check(Weights);
            const FTransform ToRoot=Mesh->GetComponentTransform().GetRelativeTransform(Actor->GetRootComponent()->GetComponentTransform());
            TArray<FBox> BoneBounds;BoneBounds.Init(FBox(ForceInit),Mesh->GetNumBones());uint32 Checked=0;
            for(const auto& Section:LOD.RenderSections)for(uint32 V=Section.BaseVertexIndex;V<Section.BaseVertexIndex+Section.NumVertices;++V)
            {
                int32 Bone=INDEX_NONE,Count=0;
                for(uint32 K=0;K<Weights->GetMaxBoneInfluences();++K)if(Weights->GetBoneWeight(V,K))
                {check(Weights->GetBoneWeight(V,K)==255);Bone=Section.BoneMap[Weights->GetBoneIndex(V,K)];++Count;}
                check(Count==1 && Bone!=INDEX_NONE);BoneBounds[Bone]+=LOD.StaticVertexBuffers.PositionVertexBuffer.VertexPosition(V);++Checked;
            }
            check(Checked==LOD.GetNumVertices());TotalVertices+=Checked;
            for(int32 I=0;I<=Steps;++I)
            {
                Mesh->SetPosition(Anim->SequenceLength*float(I)/Steps,false);Mesh->TickAnimation(0.f,false);Mesh->RefreshBoneTransforms();
                TArray<FMatrix> Matrices;Mesh->CacheRefToLocalMatrices(Matrices);
                for(int32 Bone=0;Bone<BoneBounds.Num();++Bone)if(BoneBounds[Bone].IsValid)Bounds+=BoneBounds[Bone].TransformBy(Matrices[Bone]).TransformBy(ToRoot);
            }
        }
        Result->SetStringField(TEXT("boundsMethod"),TEXT("All components: all vertices verified one-bone rigid; sampled per-bone bind boxes"));
        Result->SetNumberField(TEXT("sampleRateHz"),SampleRate);
        Result->SetNumberField(TEXT("rigidVerticesVerified"),double(TotalVertices));Result->SetNumberField(TEXT("skeletalMeshComponents"),Meshes.Num());
        // Margin handles between-sample motion; curved/fast paths require denser sampling and visual review.
        Bounds=Bounds.ExpandBy(Margin+Bounds.GetExtent().GetMax()*.01f);check(Bounds.IsValid);
        USCS_Node* OwnedNode=nullptr;
        for(auto* Node:BP->SimpleConstructionScript->GetAllNodes())if(Node->GetVariableName()==TEXT("AssemblyMotionCollision"))OwnedNode=Node;
        FName SelectedName=NAME_None;
        for(auto* C:Components)if(auto* Box=Cast<UBoxComponent>(C))
        {
            if(Box->GetCollisionEnabled()==ECollisionEnabled::NoCollision)continue;
            const FBox Local=Bounds.TransformBy(Actor->GetRootComponent()->GetComponentTransform()).TransformBy(Box->GetComponentTransform().Inverse());
            const FBox Container(-Box->GetUnscaledBoxExtent(),Box->GetUnscaledBoxExtent());
            if(Container.IsInsideOrOn(Local.Min) && Container.IsInsideOrOn(Local.Max)){SelectedName=Box->GetFName();break;}
        }
        if(OwnedNode || !Existing)
        {
            if(!OwnedNode)
            {
                OwnedNode=BP->SimpleConstructionScript->CreateNode(UBoxComponent::StaticClass(),TEXT("AssemblyMotionCollision"));
                check(BP->SimpleConstructionScript->GetRootNodes().Num()==1);BP->SimpleConstructionScript->GetRootNodes()[0]->AddChildNode(OwnedNode);Added=true;
            }
            auto* Box=CastChecked<UBoxComponent>(OwnedNode->ComponentTemplate);
            Box->SetBoxExtent(Bounds.GetExtent());Box->SetRelativeLocation(Bounds.GetCenter());Box->SetCollisionProfileName(*Profile);Box->SetCollisionEnabled(ECollisionEnabled::QueryAndPhysics);
            Compile(BP);Save(BP);SelectedName=OwnedNode->GetVariableName();
        }
        Result->SetStringField(TEXT("centerRootSpace"),Bounds.GetCenter().ToString());Result->SetStringField(TEXT("extentRootSpace"),Bounds.GetExtent().ToString());Result->SetNumberField(TEXT("samples"),Steps+1);Result->SetStringField(TEXT("profile"),Profile);
        World->DestroyActor(Actor);
        auto* Probe=World->SpawnActor<AActor>(BP->GeneratedClass);
        TArray<UBoxComponent*> Boxes;Probe->GetComponents(Boxes);UBoxComponent* ProbeBox=nullptr;
        for(auto* Box:Boxes)if(Box->GetFName()==SelectedName)ProbeBox=Box;
        bool Covers=false,HitBox=false;
        if(ProbeBox && ProbeBox->GetCollisionEnabled()!=ECollisionEnabled::NoCollision)
        {
            const FBox Local=Bounds.TransformBy(Probe->GetRootComponent()->GetComponentTransform()).TransformBy(ProbeBox->GetComponentTransform().Inverse());
            const FBox Container(-ProbeBox->GetUnscaledBoxExtent()-FVector(.001f),ProbeBox->GetUnscaledBoxExtent()+FVector(.001f));
            Covers=Container.IsInsideOrOn(Local.Min) && Container.IsInsideOrOn(Local.Max);
            FHitResult Hit;const FVector Center=ProbeBox->GetComponentLocation();const float Distance=ProbeBox->Bounds.SphereRadius+100.f;
            HitBox=World->LineTraceSingleByChannel(Hit,Center+FVector(Distance,0,0),Center-FVector(Distance,0,0),ECC_Visibility) && Hit.GetActor()==Probe;
        }
        Passed=Covers && HitBox;
        Result->SetBoolField(TEXT("fullMotionBoundsCovered"),Covers);Result->SetBoolField(TEXT("visibilityTracePassed"),HitBox);
        if(!Passed)Result->SetStringField(TEXT("diagnostic"),TEXT("Existing collision does not prove full-motion coverage and visibility blocking. Inspect it; preserve user collision and add or adjust a motion box only within the authorized scope."));
    }
    Result->SetBoolField(TEXT("addedBox"),Added);Result->SetBoolField(TEXT("passed"),Passed);FString Json;FJsonSerializer::Serialize(Result,TJsonWriterFactory<>::Create(&Json));check(FFileHelper::SaveStringToFile(Json,*Report));
    if(Passed){UE_LOG(LogTemp,Display,TEXT("MOTION_COLLISION_OK added=%d"),Added);}
    else {UE_LOG(LogTemp,Error,TEXT("MOTION_COLLISION_REVIEW_REQUIRED %s"),*Path);}
    return Passed?0:1;
}
}
