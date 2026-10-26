#pragma once
#include "CoreMinimal.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
#include "Misc/PackageName.h"
#include "InputCoreTypes.h"

// Runtime parameters keep one compiled editor plugin reusable across models.
inline bool OpenModelValidId(const FString& Id)
{
    if (Id.IsEmpty() || !(FChar::IsAlpha(Id[0]) || Id[0] == TEXT('_'))) return false;
    for (TCHAR C : Id) if (!(FChar::IsAlnum(C) || C == TEXT('_'))) return false;
    return true;
}
inline FString OpenModelObjectId()
{
    FString Id = TEXT("OpenModel");
    FParse::Value(FCommandLine::Get(), TEXT("ObjectId="), Id);
    checkf(OpenModelValidId(Id), TEXT("ObjectId must be a stable asset identifier: %s"), *Id);
    return Id;
}
inline FString OpenModelRoot()
{
    FString Root = FString(TEXT("/Game/")) + OpenModelObjectId();
    FParse::Value(FCommandLine::Get(), TEXT("Root="), Root);
    checkf(FPackageName::IsValidLongPackageName(Root), TEXT("Invalid Root: %s"), *Root);
    return Root;
}
inline FKey OpenModelHotkey()
{
    FString Name = TEXT("N");
    FParse::Value(FCommandLine::Get(), TEXT("Hotkey="), Name);
    FKey Key(*Name);
    checkf(Key.IsValid(), TEXT("Unknown Unreal input key: %s"), *Name);
    return Key;
}
inline FString OpenModelLibraryName(const FString& Id) { return FString(TEXT("OpenModel_")) + Id; }
inline FString OpenModelLibraryFolder(const FString& Id)
{
    return FString(TEXT("/JC_CustomAssets/ObjectLibrary/Exhibition/OpenModel/")) + OpenModelLibraryName(Id);
}
inline FString OpenModelLibraryBlueprint(const FString& Id)
{
    const FString Name = OpenModelLibraryName(Id);
    return OpenModelLibraryFolder(Id) + TEXT("/") + Name + TEXT(".") + Name;
}
inline FString OpenModelLiteral(const TCHAR* Value)
{
    FString Result(Value);
    Result.ReplaceInline(TEXT("/Game/OpenModel"), *OpenModelRoot());
    return Result;
}
