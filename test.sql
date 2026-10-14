    SELECT
        DECODE(:group_by, '1', Ias_Bill_Mst.C_Code, '2', Ias_Bill_Mst.Rep_Code, '3', Ias_Itm_Mst.G_Code) AS V_Code,
        Ias_Itm_Mst.I_Code,
        Ias_Bill_Dtl.Itm_Unt,
        DECODE(:local_rep, 0, Ias_Bill_Mst.Bill_Currency, :local_cur) AS Bill_Currency,
        SUM(Ias_Bill_Dtl.P_Qty) AS P_Qty,
        SUM(Ias_Bill_Dtl.Free_Qty * NVL(Ias_Bill_Dtl.P_Size, 0)) AS Free_Qty,
        0 AS Rt_Qty,
        0 AS Free_Rt_Qty,
        SUM(((NVL(Ias_Bill_Dtl.I_Price,0) - NVL(Ias_Bill_Dtl.Dis_Amt,0)) 
           + (NVL(Ias_Bill_Dtl.Othr_Amt,0) + NVL(Ias_Bill_Dtl.Vat_Amt,0)))
           * NVL(Ias_Bill_Dtl.I_Qty,0) * NVL(Ias_Bill_Mst.Bill_Rate, 1)) AS Bill_Amt_Loc,
        0 AS Rt_Bill_Amt_Loc
    FROM
        IAS_BILL_MST Ias_Bill_Mst
        JOIN IAS_BILL_DTL Ias_Bill_Dtl ON Ias_Bill_Mst.Bill_Ser = Ias_Bill_Dtl.Bill_Ser
        JOIN IAS_ITM_MST Ias_Itm_Mst ON Ias_Bill_Dtl.I_Code = Ias_Itm_Mst.I_Code
        JOIN IAS_ITM_DTL Ias_Itm_Dtl ON Ias_Bill_Dtl.I_Code = Ias_Itm_Dtl.I_Code AND Ias_Bill_Dtl.Itm_Unt = Ias_Itm_Dtl.Itm_Unt
    WHERE
        Ias_Bill_Mst.Bill_Date >= TO_DATE(:start_date, 'YYYY-MM-DD')
        AND Ias_Bill_Mst.Bill_Date <= TO_DATE(:end_date, 'YYYY-MM-DD')
        AND Ias_Bill_Mst.CC_Code IN ('11', '12')
    GROUP BY
        DECODE(:group_by, '1', Ias_Bill_Mst.C_Code, '2', Ias_Bill_Mst.Rep_Code, '3', Ias_Itm_Mst.G_Code),
        Ias_Itm_Mst.I_Code,
        Ias_Bill_Dtl.Itm_Unt,
        DECODE(:local_rep, 0, Ias_Bill_Mst.Bill_Currency, :local_cur)

    UNION ALL

    -- مردودات الفواتير
    SELECT
        DECODE(:group_by, '1', Ias_Rt_Bill_Mst.C_Code, '2', Ias_Rt_Bill_Mst.Rep_Code, '3', Ias_Itm_Mst.G_Code) AS V_Code,
        Ias_Itm_Mst.I_Code,
        Ias_Rt_Bill_Dtl.Itm_Unt,
        DECODE(:local_rep, 0, Ias_Rt_Bill_Mst.Rt_Bill_Currency, :local_cur) AS Bill_Currency,
        0 AS P_Qty,
        0 AS Free_Qty,
        SUM(Ias_Rt_Bill_Dtl.P_Qty) AS Rt_Qty,
        SUM(Ias_Rt_Bill_Dtl.Free_Qty * NVL(Ias_Rt_Bill_Dtl.P_Size, 0)) AS Free_Rt_Qty,
        0 AS Bill_Amt_Loc,
        SUM(((NVL(Ias_Rt_Bill_Dtl.I_Price,0) - NVL(Ias_Rt_Bill_Dtl.Dis_Amt,0)) 
           + (NVL(Ias_Rt_Bill_Dtl.Othr_Amt,0) + NVL(Ias_Rt_Bill_Dtl.Vat_Amt,0)))
           * NVL(Ias_Rt_Bill_Dtl.I_Qty,0) * NVL(Ias_Rt_Bill_Mst.Rt_Bill_Rate, 1)) AS Rt_Bill_Amt_Loc
    FROM
        IAS_RT_BILL_MST Ias_Rt_Bill_Mst
        JOIN IAS_RT_BILL_DTL Ias_Rt_Bill_Dtl ON Ias_Rt_Bill_Mst.Rt_Bill_Ser = Ias_Rt_Bill_Dtl.Rt_Bill_Ser
        JOIN IAS_ITM_MST Ias_Itm_Mst ON Ias_Rt_Bill_Dtl.I_Code = Ias_Itm_Mst.I_Code
        JOIN IAS_ITM_DTL Ias_Itm_Dtl ON Ias_Rt_Bill_Dtl.I_Code = Ias_Itm_Dtl.I_Code AND Ias_Rt_Bill_Dtl.Itm_Unt = Ias_Itm_Dtl.Itm_Unt
    WHERE
        Ias_Rt_Bill_Mst.Rt_Bill_Date >= TO_DATE(:start_date, 'YYYY-MM-DD')
        AND Ias_Rt_Bill_Mst.Rt_Bill_Date <= TO_DATE(:end_date, 'YYYY-MM-DD')
        AND Ias_Rt_Bill_Mst.CC_Code IN ('11', '12')
    GROUP BY
        DECODE(:group_by, '1', Ias_Rt_Bill_Mst.C_Code, '2', Ias_Rt_Bill_Mst.Rep_Code, '3', Ias_Itm_Mst.G_Code),
        Ias_Itm_Mst.I_Code,
        Ias_Rt_Bill_Dtl.Itm_Unt,
        DECODE(:local_rep, 0, Ias_Rt_Bill_Mst.Rt_Bill_Currency, :local_cur)
) Sales
