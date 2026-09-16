namespace OpenModelBuild
{
static int32 SplitUserVariable()
{
    auto* BP=LoadObject<UBlueprint>(nullptr,TEXT("/Game/OpenModel/NoUMG/BP_ExplodedAssembly_NoUMG.BP_ExplodedAssembly_NoUMG"));check(BP);
    UEdGraph* G=nullptr;UK2Node_FunctionEntry* Entry=nullptr;
    for(auto* Graph:BP->FunctionGraphs){check(Graph->GetFName()!=TEXT("UserVariable"));if(Graph->GetFName()==TEXT("ControlAssembly"))G=Graph;}
    check(G);for(auto* N:G->Nodes)if(auto* E=Cast<UK2Node_FunctionEntry>(N))Entry=E;check(Entry);
    // Remove any packaging-era input setters while preserving the original initialization branch.
    auto* Then=P(Entry,UEdGraphSchema_K2::PN_Then);
    while(Then->LinkedTo.Num()==1 && Then->LinkedTo[0]->GetOwningNode()->IsA<UK2Node_VariableSet>())
    {
        auto* N=CastChecked<UK2Node_VariableSet>(Then->LinkedTo[0]->GetOwningNode());
        check(N->VariableReference.GetMemberName()==TEXT("Progress") || N->VariableReference.GetMemberName()==TEXT("FullTravelDuration"));
        auto* Next=P(N,UEdGraphSchema_K2::PN_Then);check(Next->LinkedTo.Num()==1);auto* Dest=Next->LinkedTo[0];
        N->DestroyNode();Wire(Then,Dest);
    }
    auto* Flag=P(Entry,TEXT("SetProgress"));auto* Value=P(Entry,TEXT("NewProgress"));
    check(Flag->LinkedTo.Num()==1 && Value->LinkedTo.Num()==1);
    auto* Request=CastChecked<UK2Node_IfThenElse>(Flag->LinkedTo[0]->GetOwningNode());
    auto* Requested=CastChecked<UK2Node_VariableSet>(Value->LinkedTo[0]->GetOwningNode());
    auto* Else=P(Request,UEdGraphSchema_K2::PN_Else);check(Else->LinkedTo.Num()==1);
    auto* Detect=Else->LinkedTo[0];auto Incoming=P(Request,UEdGraphSchema_K2::PN_Execute)->LinkedTo;
    Request->DestroyNode();Requested->DestroyNode();for(auto* From:Incoming)Wire(From,Detect);
    for(FName Name:{FName(TEXT("Progress")),FName(TEXT("FullTravelDuration")),FName(TEXT("SetProgress")),FName(TEXT("NewProgress"))})
        if(Entry->FindPin(Name))Entry->RemoveUserDefinedPinByName(Name);
    Entry->NodePosX=-350;Entry->NodePosY=0;
    for(auto* N:G->Nodes)if(N->NodeComment.Contains(TEXT("second custom function")))N->NodeComment=TEXT("Constant progress speed; no Timeline");
    auto User=Func(BP,TEXT("UserVariable"));
    User.Entry->CreateUserDefinedPin(TEXT("Progress"),FloatT(),EGPD_Output);
    User.Entry->CreateUserDefinedPin(TEXT("FullTravelDuration"),FloatT(),EGPD_Output);
    auto* Progress=Set(User.Graph,TEXT("Progress"));auto* Duration=Set(User.Graph,TEXT("FullTravelDuration"));
    Wire(P(User.Entry,TEXT("Progress")),P(Progress,TEXT("Progress")));
    Wire(P(User.Entry,TEXT("FullTravelDuration")),P(Duration,TEXT("FullTravelDuration")));
    Exec(User.Entry,Progress);Exec(Progress,Duration);Exec(Duration,User.Result);
    User.Entry->NodePosX=0;User.Entry->NodePosY=0;Progress->NodePosX=350;Progress->NodePosY=0;
    Duration->NodePosX=700;Duration->NodePosY=0;User.Result->NodePosX=1050;User.Result->NodePosY=0;
    FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(BP);
    for(auto* Graph:BP->UbergraphPages)
    {
        auto Nodes=Graph->Nodes;
        for(auto* N:Nodes)if(auto* C=Cast<UK2Node_CallFunction>(N))C->ReconstructNode();
        for(auto* N:Nodes)if(N->IsA<UK2Node_VariableGet>())
        {bool Used=false;for(auto* Pin:N->Pins)Used|=Pin->LinkedTo.Num()>0;if(!Used)N->DestroyNode();}
    }
    Compile(BP);check(BP->FunctionGraphs.Num()==2);
    check(P(Entry,UEdGraphSchema_K2::PN_Then)->LinkedTo[0]->GetOwningNode()->IsA<UK2Node_IfThenElse>());
    Save(BP);UE_LOG(LogTemp,Display,TEXT("USER_VARIABLE_SPLIT_OK"));return 0;
}
}
