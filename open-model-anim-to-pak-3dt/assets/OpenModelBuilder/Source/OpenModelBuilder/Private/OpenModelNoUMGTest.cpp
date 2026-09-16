#include "CoreMinimal.h"
#include "Misc/AutomationTest.h"
#include "Tests/AutomationEditorCommon.h"
#include "Editor.h"
#include "EngineUtils.h"
#include "GameFramework/PlayerController.h"
#include "Blueprint/UserWidget.h"
#include "UObject/UnrealType.h"
#include "PropertyEditorModule.h"
#include "ISinglePropertyView.h"
#include "PropertyHandle.h"
#include "Misc/Paths.h"
#include "UnrealClient.h"

#if WITH_DEV_AUTOMATION_TESTS
class FNoUMGInteraction : public IAutomationLatentCommand
{
    FAutomationTestBase* Test; int32 Stage=0; double Start=0,Last=0; float Before=0;
    TWeakObjectPtr<AActor> Actor; TWeakObjectPtr<APlayerController> PC;
    TSharedPtr<ISinglePropertyView> PropertyView;
    float P(){return FindFProperty<FFloatProperty>(Actor->GetClass(),TEXT("Progress"))->GetPropertyValue_InContainer(Actor.Get());}
    bool Auto(){return FindFProperty<FBoolProperty>(Actor->GetClass(),TEXT("bAutoPlaying"))->GetPropertyValue_InContainer(Actor.Get());}
    void Key(EInputEvent Event){PC->InputKey(EKeys::N,Event,Event==IE_Pressed?1.f:0.f,false);}
    void Edit(float Value){Test->TestTrue(TEXT("Details property handle accepts value"),PropertyView->GetPropertyHandle()->SetValue(Value)==FPropertyAccess::Success);}
    void Shot(const TCHAR* Name){FString Dir;FParse::Value(FCommandLine::Get(),TEXT("NoUMGReport="),Dir);if(!Dir.IsEmpty())FScreenshotRequest::RequestScreenshot(Dir/Name,true,false);}
public:
    explicit FNoUMGInteraction(FAutomationTestBase* T):Test(T){}
    virtual bool Update() override
    {
        const double Now=FPlatformTime::Seconds();if(Start==0)Start=Now;
        if(Now-Start>45){Test->AddError(FString::Printf(TEXT("NoUMG PIE timed out: stage=%d actor=%s pc=%s input=%s receive=%d"),Stage,*GetNameSafe(Actor.Get()),*GetNameSafe(PC.Get()),Actor.IsValid()?*GetNameSafe(Actor->InputComponent):TEXT("none"),Actor.IsValid()?int32(Actor->AutoReceiveInput):-1));return true;}
        auto* World=GEditor->PlayWorld;if(!World)return false;
        if(Stage==0)
        {
            for(TActorIterator<AActor> It(World);It;++It)if(It->GetClass()->GetName()==TEXT("BP_ExplodedAssembly_NoUMG_C"))Actor=*It;
            PC=World->GetFirstPlayerController();if(!Actor.IsValid()||!PC.IsValid()||!Actor->InputComponent)return false;
            PropertyView=FModuleManager::LoadModuleChecked<FPropertyEditorModule>(TEXT("PropertyEditor")).CreateSingleProperty(Actor.Get(),TEXT("Progress"),FSinglePropertyParams());
            if(!PropertyView.IsValid()||!PropertyView->GetPropertyHandle().IsValid()){Test->AddError(TEXT("No runtime Progress property editor"));return true;}
            int32 Widgets=0;for(TObjectIterator<UUserWidget> It;It;++It)if(It->GetWorld()==World && It->IsInViewport())++Widgets;
            Test->TestEqual(TEXT("No UMG in play world"),Widgets,0);
            Test->TestTrue(TEXT("View follows controllable pawn rather than fixed camera"),PC->GetPawn()!=nullptr && PC->GetViewTarget()==PC->GetPawn());
            Test->TestTrue(TEXT("Starts assembled"),FMath::IsNearlyZero(P(),1.e-5f));Last=Now;Stage=1;return false;
        }
        if(Now-Last<.4)return false;
        switch(Stage)
        {
        case 1: Shot(TEXT("assembled.png"));Key(IE_Pressed);Stage=2;break;
        case 2: Key(IE_Released);Test->TestTrue(TEXT("N starts outward on actual world ticks"),P()>.01f&&Auto());Before=P();Stage=3;break;
        case 3: Key(IE_Pressed);Before=P();Stage=4;break;
        case 4: Key(IE_Released);Test->TestTrue(TEXT("Second N reverses from current progress"),P()<Before);Edit(.5f);Stage=5;break;
        case 5: Test->TestTrue(TEXT("Live Details edit takes over and holds"),FMath::IsNearlyEqual(P(),.5f,1.e-5f)&&!Auto());Shot(TEXT("half.png"));Edit(1.f);Stage=6;break;
        case 6: Test->TestTrue(TEXT("Details reaches exact exploded end"),P()==1.f&&!Auto());Shot(TEXT("exploded.png"));Key(IE_Pressed);Stage=7;break;
        case 7: Key(IE_Released);Test->TestTrue(TEXT("N assembles from full end"),P()<1.f&&Auto());Edit(.283f);Stage=8;break;
        case 8: Test->TestTrue(TEXT("Arbitrary Details progress"),FMath::IsNearlyEqual(P(),.283f,1.e-5f)&&!Auto());Key(IE_Pressed);Stage=9;break;
        case 9: Key(IE_Released);Test->TestTrue(TEXT("N from manual middle resumes outward"),P()>.283f&&Auto());Edit(0.f);Stage=10;break;
        case 10: Test->TestTrue(TEXT("Details restores assembled end"),P()==0.f&&!Auto());return true;
        }
        Last=Now;return false;
    }
};
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FOpenModelNoUMGPIE,"OpenModel.NoUMG.PIE",EAutomationTestFlags::EditorContext|EAutomationTestFlags::EngineFilter)
bool FOpenModelNoUMGPIE::RunTest(const FString& Parameters)
{
    ADD_LATENT_AUTOMATION_COMMAND(FEditorLoadMap(TEXT("/Game/OpenModel/NoUMG/Demo_ExplodedAssembly_NoUMG")));
    ADD_LATENT_AUTOMATION_COMMAND(FWaitForShadersToFinishCompiling());
    ADD_LATENT_AUTOMATION_COMMAND(FStartPIECommand(false));
    FAutomationTestFramework::Get().EnqueueLatentCommand(MakeShareable(new FNoUMGInteraction(this)));
    ADD_LATENT_AUTOMATION_COMMAND(FEndPlayMapCommand());return true;
}
#endif
