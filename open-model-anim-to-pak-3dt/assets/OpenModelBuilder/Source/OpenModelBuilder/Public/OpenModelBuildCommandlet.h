#pragma once
#include "CoreMinimal.h"
#include "Commandlets/Commandlet.h"
#include "OpenModelBuildCommandlet.generated.h"

UCLASS()
class UOpenModelBuildCommandlet : public UCommandlet
{
    GENERATED_BODY()
public:
    UOpenModelBuildCommandlet();
    virtual int32 Main(const FString& Params) override;
};

UCLASS()
class UOpenModelValidateCommandlet : public UCommandlet
{
    GENERATED_BODY()
public:
    UOpenModelValidateCommandlet();
    virtual int32 Main(const FString& Params) override;
};

UCLASS()
class UOpenModelNoUMGCommandlet : public UCommandlet
{
    GENERATED_BODY()
public:
    UOpenModelNoUMGCommandlet();
    virtual int32 Main(const FString& Params) override;
};
