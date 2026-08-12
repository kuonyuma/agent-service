#include <stdio.h>

// 交换两个元素
void swap(int* a, int* b) {
    int t = *a;
    *a = *b;
    *b = t;
}

// 分区函数，选择最后一个元素作为基准值
int partition(int arr[], int low, int high) {
    int pivot = arr[high]; // 基准值
    int i = (low - 1);     // 较小元素的索引

    for (int j = low; j <= high - 1; j++) {
        // 如果当前元素小于或等于基准值
        if (arr[j] <= pivot) {
            i++; // 增加较小元素的索引
            swap(&arr[i], &arr[j]);
        }
    }
    swap(&arr[i + 1], &arr[high]);
    return (i + 1);
}

// 快速排序主函数
void quickSort(int arr[], int low, int high) {
    if (low < high) {
        // pi 是分区后的基准索引
        int pi = partition(arr, low, high);

        // 分别对基准左右两侧的子数组进行递归排序
        quickSort(arr, low, pi - 1);
        quickSort(arr, pi + 1, high);
    }
}

// 打印数组
void printArray(int arr[], int n) {
    for (int i = 0; i < n; ++i)
        printf("%d ", arr[i]);
    printf("\n");
}

int main() {
    int arr[] = {12, 11, 13, 5, 6, 7};
    int n = sizeof(arr) / sizeof(arr[0]);

    printf("排序前的数组: \n");
    printArray(arr, n);

    quickSort(arr, 0, n - 1);

    printf("排序后的数组: \n");
    printArray(arr, n);

    return 0;
}
