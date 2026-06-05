from unilabos.resources.itemized_carrier import Bottle, BottleCarrier
# 工厂函数
"""加样头（大）"""
def YB_DosingHead_L(
    name: str,
    diameter: float = 20.0,
    height: float = 100.0,
    max_volume: float = 30000.0,  # 30mL
    barcode: str = None,
) -> Bottle:
    """创建粉末瓶"""
    return Bottle(
        name=name,
        diameter=diameter,# 未知
        height=height,
        max_volume=max_volume,
        barcode=barcode,
        model="YB_DosingHead_L",
    )

"""250mL普通液"""
def YB_NormalLiq_250mL_Bottle(
    name: str,
    diameter: float = 40.0,
    height: float = 70.0,
    max_volume: float = 50000.0,  # 50mL
    barcode: str = None,
) -> Bottle:
    """创建液体瓶"""
    return Bottle(
        name=name,
        diameter=diameter,
        height=height,
        max_volume=max_volume,
        barcode=barcode,
        model="YB_NormalLiq_250mL_Bottle",
    )

"""100mL普通液"""
def YB_NormalLiq_100mL_Bottle(
    name: str,
    diameter: float = 50.0,
    height: float = 90.0,
    max_volume: float = 100000.0,  # 100mL
    barcode: str = None,
) -> Bottle:
    """创建100mL普通液瓶"""
    return Bottle(
        name=name,
        diameter=diameter,
        height=height,
        max_volume=max_volume,
        barcode=barcode,
        model="YB_NormalLiq_100mL_Bottle",
    )

"""100mL高粘液"""
def YB_HighVis_100mL_Bottle(
    name: str,
    diameter: float = 50.0,
    height: float = 90.0,
    max_volume: float = 100000.0,  # 100mL
    barcode: str = None,
) -> Bottle:
    """创建100mL高粘液瓶"""
    return Bottle(
        name=name,
        diameter=diameter,
        height=height,
        max_volume=max_volume,
        barcode=barcode,
        model="YB_HighVis_100mL_Bottle",
    )

"""250mL高粘液"""
def YB_HighVis_250mL_Bottle(
    name: str,
    diameter: float = 40.0,
    height: float = 70.0,
    max_volume: float = 50000.0,  # 50mL
    barcode: str = None,
) -> Bottle:
    """创建250mL高粘液瓶"""
    return Bottle(
        name=name,
        diameter=diameter,
        height=height,
        max_volume=max_volume,
        barcode=barcode,
        model="YB_HighVis_250mL_Bottle",
    )

"""5mL分液瓶"""
def YB_Vial_5mL(
    name: str,
    diameter: float = 20.0,
    height: float = 50.0,
    max_volume: float = 5000.0,  # 5mL
    barcode: str = None,
) -> Bottle:
    """创建5mL分液瓶"""
    return Bottle(
        name=name,
        diameter=diameter,
        height=height,
        max_volume=max_volume,
        barcode=barcode,
        model="YB_Vial_5mL",
    )

"""20mL分液瓶"""
def YB_Vial_20mL(
    name: str,
    diameter: float = 30.0,
    height: float = 65.0,
    max_volume: float = 20000.0,  # 20mL
    barcode: str = None,
) -> Bottle:
    """创建20mL分液瓶"""
    return Bottle(
        name=name,
        diameter=diameter,
        height=height,
        max_volume=max_volume,
        barcode=barcode,
        model="YB_Vial_20mL",
    )

"""配液瓶(小)"""
def YB_PrepBottle_15mL(
    name: str,
    diameter: float = 35.0,
    height: float = 60.0,
    max_volume: float = 15000.0,  # 15mL
    barcode: str = None,
) -> Bottle:
    """创建配液瓶(小)"""
    return Bottle(
        name=name,
        diameter=diameter,
        height=height,
        max_volume=max_volume,
        barcode=barcode,
        model="YB_PrepBottle_15mL",
    )

"""配液瓶(大)"""
def YB_PrepBottle_60mL(
    name: str,
    diameter: float = 40.0,
    height: float = 80.0,
    max_volume: float = 60000.0,  # 60mL
    barcode: str = None,
) -> Bottle:
    """创建配液瓶(大)"""
    return Bottle(
        name=name,
        diameter=diameter,
        height=height,
        max_volume=max_volume,
        barcode=barcode,
        model="YB_PrepBottle_60mL",
    )

"""5000uL枪头"""
def YB_Tip_5000uL(
    name: str,
    diameter: float = 10.0,
    height: float = 50.0,
    max_volume: float = 5000.0,  # 5mL
    barcode: str = None,
) -> Bottle:
    """创建枪头"""
    return Bottle(
        name=name,
        diameter=diameter,
        height=height,
        max_volume=max_volume,
        barcode=barcode,
        model="YB_Tip_5000uL",
    ) 

"""1000uL枪头"""
def YB_Tip_1000uL(
    name: str,
    diameter: float = 10.0,
    height: float = 50.0,
    max_volume: float = 1000.0,  # 1mL
    barcode: str = None,
) -> Bottle:
    """创建枪头"""
    return Bottle(
        name=name,
        diameter=diameter,
        height=height,
        max_volume=max_volume,
        barcode=barcode,
        model="YB_Tip_1000uL",
    )    

"""50uL枪头"""
def YB_Tip_50uL(
    name: str,
    diameter: float = 10.0,
    height: float = 50.0,
    max_volume: float = 50.0,  # 50uL
    barcode: str = None,
) -> Bottle:
    """创建枪头"""
    return Bottle(
        name=name,
        diameter=diameter,
        height=height,
        max_volume=max_volume,
        barcode=barcode,
        model="YB_Tip_50uL",
    ) 