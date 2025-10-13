@{
    RootModule = 'UserDefinedToolkit.psm1'
    ModuleVersion = '1.1.0'
    GUID = 'f81d180f-d0c8-4ccb-8b10-39280a2566e5'
    Author = 'Yu-Chan Chen'
    Copyright = '(c) 2025 Yu-Chan Chen. All rights reserved.'
    Description = 'This module provides custom functions for managing processes and firewall rules in a Windows environment.'
    PowerShellVersion = '5.1'
    FunctionsToExport = @('Stop-ProcessTree', 'Add-FirewallRule', 'Remove-FirewallRule')
}